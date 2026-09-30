"""Historical OHLCV service — Yahoo / MOEX / CCXT / Alpaca / runtime chart buffer."""

from __future__ import annotations

import asyncio
import math
import os
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Iterable

from microkernel_core import BaseService, KernelEvent

from engine.adapters.alpaca_adapter import AlpacaMarketDataAdapter
from engine.adapters.ccxt_adapter import CcxtMarketDataAdapter
from engine.adapters.moex_adapter import MoexMarketDataAdapter
from engine.adapters.yahoo_adapter import YahooMarketDataAdapter
from engine.market import Candle, candle_to_market_payload
from host.events import publish_market_candle, publish_market_history_candle

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore
    from engine.universe import Instrument

_CRYPTO_CLASSES = {"CRYPTO", "SPOT", "BINANCE", "BITGET", "BYBIT", "OKX", "KRAKEN"}
_US_CLASSES = {"US", "NASDAQ", "NYSE", "ALPACA"}
_INTRADAY_PROVIDERS = {"ccxt", "alpaca", "runtime", "live", "chart"}


def _infer_provider(ticker: str, class_code: str = "") -> str:
    """Match the feed family that typically draws the main Desk chart."""
    s = str(ticker or "").strip().upper().replace("-", "").replace("_", "")
    v = str(class_code or "").strip().upper()
    if v in _CRYPTO_CLASSES or "/" in str(ticker or ""):
        return "ccxt"
    # Compact crypto symbols (BTCUSDT) without class_code / slash.
    for quote in ("USDT", "USDC", "BUSD", "TUSD", "FDUSD"):
        if len(s) > len(quote) and s.endswith(quote):
            return "ccxt"
    if v in _US_CLASSES:
        return "alpaca"
    return ""


class HistoricalDataService(BaseService):
    """
    On-demand history fetch (not a live stream).

    REPL / phantom / universe metrics call ``fetch`` / ``metrics_for`` / ``replay``.
    Providers: yahoo, moex, ccxt, alpaca, runtime (live CandleStore on the chart).
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self._yahoo: YahooMarketDataAdapter | None = None
        self._moex: MoexMarketDataAdapter | None = None
        self._ccxt: CcxtMarketDataAdapter | None = None
        self._alpaca: AlpacaMarketDataAdapter | None = None
        self._last_fetch: dict[str, Any] = {}
        self._cache: dict[tuple[Any, ...], tuple[float, list[Candle]]] = {}
        self._last_remote_at = 0.0
        self._fetch_lock = asyncio.Lock()
        self._last_replay: dict[str, Any] = {}

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "default_provider": "auto",  # auto | yahoo | moex | ccxt | alpaca | runtime
            "default_timeframe": "1d",
            "intraday_timeframe": "1m",
            "default_limit": 120,
            "publish_events": True,
            "yahoo_suffix": "",  # e.g. .ME for MOEX tickers on Yahoo
            "cache_ttl_sec": 900,
            "disk_cache_dir": "data/ohlc_cache",
            "disk_cache_ttl_sec": 86_400,
            "min_fetch_interval_sec": 0.25,
            "metrics_bars": 40,
            "replay_delay_sec": 0.0,
            "replay_pause_live": True,
        }

    async def on_initialize(self) -> None:
        self._yahoo = YahooMarketDataAdapter()
        self._moex = MoexMarketDataAdapter()

    async def start(self) -> None:
        self.is_running = True
        if self._yahoo and self._yahoo.available:
            await self._yahoo.connect()
        if self._moex and self._moex.available:
            try:
                await self._moex.connect()
            except Exception:
                pass

    async def stop(self) -> None:
        self.is_running = False
        if self._yahoo:
            await self._yahoo.disconnect()
        if self._moex:
            await self._moex.disconnect()
        if self._ccxt:
            try:
                await self._ccxt.disconnect()
            except Exception:
                pass
        if self._alpaca:
            try:
                await self._alpaca.disconnect()
            except Exception:
                pass
        self._cache.clear()

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        status.update(
            {
                "yahoo": bool(self._yahoo and self._yahoo.available),
                "moex": bool(self._moex and self._moex.available),
                "ccxt": bool(self._ccxt and getattr(self._ccxt, "available", True)),
                "alpaca": bool(self._alpaca and self._alpaca.available),
                "runtime": bool(getattr(self.core, "trading", None) is not None),
                "default_provider": self.config.get("default_provider"),
                "last_fetch": dict(self._last_fetch),
                "cache_entries": len(self._cache),
                "last_replay": dict(self._last_replay),
            }
        )
        return status

    def clear_cache(self) -> None:
        self._cache.clear()

    def _resolve_provider(
        self,
        provider: str | None,
        *,
        ticker: str = "",
        class_code: str = "",
    ) -> str:
        raw = str(provider or "").strip().lower()
        if raw in {"", "auto"}:
            inferred = _infer_provider(ticker, class_code)
            if inferred:
                return inferred
            fallback = str(self.config.get("default_provider") or "yahoo").lower()
            if fallback in {"", "auto"}:
                return "yahoo"
            return fallback
        return raw

    def _yahoo_symbol(self, ticker: str, class_code: str) -> str:
        if "." in ticker:
            return ticker
        suffix = str(self.config.get("yahoo_suffix") or "").strip()
        if suffix:
            return ticker if ticker.endswith(suffix) else f"{ticker}{suffix}"
        if class_code.upper() in {"TQBR", "TQTF"}:
            return f"{ticker}.ME"
        return ticker

    def _cache_key(
        self,
        *,
        provider: str,
        ticker: str,
        class_code: str,
        timeframe: str,
        limit: int,
        start: str | None,
        end: str | None,
    ) -> tuple[Any, ...]:
        return (provider, ticker, class_code, timeframe, limit, start, end)

    async def _throttle(self) -> None:
        gap = float(self.config.get("min_fetch_interval_sec") or 0)
        if gap <= 0:
            return
        elapsed = time.monotonic() - self._last_remote_at
        if elapsed < gap:
            await asyncio.sleep(gap - elapsed)

    def _record_last_fetch(
        self,
        *,
        provider: str,
        ticker: str,
        class_code: str,
        bars: list[Candle],
        timeframe: str,
        cache: bool,
    ) -> None:
        from engine.ohlc_timeframe import median_bar_interval_sec, timeframe_to_seconds

        observed = median_bar_interval_sec(bars)
        expected = timeframe_to_seconds(timeframe)
        self._last_fetch = {
            "provider": provider,
            "ticker": ticker,
            "class_code": class_code,
            "bars": len(bars),
            "timeframe": timeframe,
            "bar_interval_sec": observed,
            "expected_interval_sec": float(expected) if expected else None,
            "cache": cache,
        }

    async def fetch(
        self,
        ticker: str,
        *,
        class_code: str = "TQBR",
        provider: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
        use_cache: bool = True,
    ) -> list[Candle]:
        from engine.ohlc_timeframe import ensure_timeframe, normalize_timeframe

        prov = self._resolve_provider(provider, ticker=ticker, class_code=class_code)
        if timeframe:
            tf = normalize_timeframe(str(timeframe))
        elif prov in _INTRADAY_PROVIDERS:
            tf = normalize_timeframe(str(self.config.get("intraday_timeframe") or "1m"))
        else:
            tf = normalize_timeframe(str(self.config.get("default_timeframe") or "1d"))
        lim = int(limit or self.config.get("default_limit") or 120)
        key = self._cache_key(
            provider=prov,
            ticker=ticker,
            class_code=class_code,
            timeframe=tf,
            limit=lim,
            start=start,
            end=end,
        )
        ttl = float(self.config.get("cache_ttl_sec") or 0)
        disk_ttl = float(self.config.get("disk_cache_ttl_sec") or 0)
        disk_root = str(self.config.get("disk_cache_dir") or "").strip()
        disk_path = None
        if use_cache and disk_ttl > 0 and disk_root and prov not in {
            "runtime",
            "live",
            "chart",
            "engine",
        }:
            from engine.ohlc_disk_cache import cache_file_path, read_disk_cache

            disk_path = cache_file_path(
                disk_root,
                provider=prov,
                ticker=ticker,
                class_code=class_code,
                timeframe=tf,
                limit=lim,
                start=start,
                end=end,
            )
        if use_cache and ttl > 0:
            hit = self._cache.get(key)
            if hit is not None:
                ts, bars = hit
                if time.monotonic() - ts <= ttl:
                    self._record_last_fetch(
                        provider=prov,
                        ticker=ticker,
                        class_code=class_code,
                        bars=bars,
                        timeframe=tf,
                        cache=True,
                    )
                    self._last_fetch["cache"] = "memory"
                    return list(bars)
        if disk_path is not None:
            from engine.ohlc_disk_cache import read_disk_cache

            disk_hit = read_disk_cache(disk_path, ttl_sec=disk_ttl)
            if disk_hit:
                if ttl > 0:
                    self._cache[key] = (time.monotonic(), list(disk_hit))
                self._record_last_fetch(
                    provider=prov,
                    ticker=ticker,
                    class_code=class_code,
                    bars=disk_hit,
                    timeframe=tf,
                    cache=True,
                )
                self._last_fetch["cache"] = "disk"
                return list(disk_hit)

        async with self._fetch_lock:
            # re-check cache under lock
            if use_cache and ttl > 0:
                hit = self._cache.get(key)
                if hit is not None:
                    ts, bars = hit
                    if time.monotonic() - ts <= ttl:
                        self._record_last_fetch(
                            provider=prov,
                            ticker=ticker,
                            class_code=class_code,
                            bars=bars,
                            timeframe=tf,
                            cache=True,
                        )
                        self._last_fetch["cache"] = "memory"
                        return list(bars)
            if disk_path is not None:
                from engine.ohlc_disk_cache import read_disk_cache

                disk_hit = read_disk_cache(disk_path, ttl_sec=disk_ttl)
                if disk_hit:
                    if ttl > 0:
                        self._cache[key] = (time.monotonic(), list(disk_hit))
                    self._record_last_fetch(
                        provider=prov,
                        ticker=ticker,
                        class_code=class_code,
                        bars=disk_hit,
                        timeframe=tf,
                        cache=True,
                    )
                    self._last_fetch["cache"] = "disk"
                    return list(disk_hit)
            await self._throttle()
            bars = await self._fetch_remote(
                ticker,
                class_code=class_code,
                provider=prov,
                timeframe=tf,
                limit=lim,
                start=start,
                end=end,
            )
            # Runtime already ensures TF; remote providers get a hard check
            # (resample allowed if exchange returned finer bars than requested).
            if prov not in {"runtime", "live", "chart", "engine"}:
                bars = ensure_timeframe(bars, tf, provider=prov, allow_resample=True)
                if lim > 0 and len(bars) > lim:
                    bars = bars[-lim:]
            self._last_remote_at = time.monotonic()
            if ttl > 0:
                self._cache[key] = (self._last_remote_at, list(bars))
            if disk_path is not None and bars:
                from engine.ohlc_disk_cache import write_disk_cache

                try:
                    write_disk_cache(
                        disk_path,
                        bars,
                        meta={
                            "provider": prov,
                            "ticker": ticker,
                            "class_code": class_code,
                            "timeframe": tf,
                            "limit": lim,
                        },
                    )
                except OSError:
                    pass

        self._record_last_fetch(
            provider=prov,
            ticker=ticker,
            class_code=class_code,
            bars=bars,
            timeframe=tf,
            cache=False,
        )
        self._last_fetch["cache"] = False
        return bars

    async def _fetch_remote(
        self,
        ticker: str,
        *,
        class_code: str,
        provider: str,
        timeframe: str,
        limit: int,
        start: str | None,
        end: str | None,
    ) -> list[Candle]:
        if provider in {"runtime", "live", "chart", "engine"}:
            return self._fetch_runtime(ticker, class_code=class_code, timeframe=timeframe, limit=limit)

        if provider == "moex":
            if self._moex is None or not self._moex.available:
                raise RuntimeError("moex adapter unavailable (aiomoex/aiohttp)")
            return await self._moex.fetch_ohlcv(
                ticker,
                timeframe,
                limit=limit,
                class_code=class_code or "TQBR",
                start=start,
                end=end,
            )

        if provider in {"ccxt", "crypto", "binance", "bybit", "okx"}:
            adapter = await self._ensure_ccxt()
            bars = await adapter.fetch_ohlcv(
                ticker,
                timeframe,
                limit=limit,
                class_code=class_code or "CRYPTO",
            )
            for b in bars:
                b.symbol = ticker
                b.class_code = class_code or b.class_code or "CRYPTO"
            return bars

        if provider in {"alpaca", "us"}:
            adapter = await self._ensure_alpaca()
            bars = await adapter.fetch_ohlcv(
                ticker,
                timeframe,
                limit=limit,
                class_code=class_code or "US",
            )
            for b in bars:
                b.symbol = ticker
                b.class_code = class_code or b.class_code or "US"
            return bars

        if provider not in {"yahoo", "yfinance"}:
            raise RuntimeError(f"unsupported_provider:{provider}")

        if self._yahoo is None or not self._yahoo.available:
            raise RuntimeError("yahoo adapter unavailable (yfinance)")
        sym = self._yahoo_symbol(ticker, class_code)
        bars = await self._yahoo.fetch_ohlcv(
            sym,
            timeframe,
            limit=limit,
            class_code=class_code,
            start=start,
            end=end,
        )
        for b in bars:
            b.symbol = ticker
            b.class_code = class_code
        return bars

    def _fetch_runtime(
        self,
        ticker: str,
        *,
        class_code: str,
        timeframe: str,
        limit: int,
    ) -> list[Candle]:
        from engine.ohlc_timeframe import ensure_timeframe, normalize_timeframe

        trading = getattr(self.core, "trading", None)
        store = getattr(trading, "candles", None) if trading is not None else None
        if store is None:
            raise RuntimeError("runtime_candles_unavailable")
        key = f"{class_code}:{ticker}" if class_code else ticker
        rows = list(store.bars(key) or store.bars(ticker) or ())
        if not rows:
            raise RuntimeError(f"runtime_candles_empty:{key}")
        tf = normalize_timeframe(timeframe)
        # Need many 1m bars to build N daily bars.
        fetch_n = int(limit) if limit > 0 else 0
        if tf not in {"1m", "1min"} and fetch_n > 0:
            fetch_n = max(fetch_n * 24 * 60, fetch_n)
        if fetch_n > 0:
            rows = rows[-fetch_n:]
        out: list[Candle] = []
        for i, bar in enumerate(rows):
            ts = bar.ts
            if ts is None:
                ts = datetime.now(UTC)
            elif ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            out.append(
                Candle(
                    symbol=ticker,
                    timeframe=str(getattr(bar, "timeframe", None) or "1m"),
                    ts=ts,
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=float(bar.volume or 0.0),
                    exchange="runtime",
                    class_code=class_code or str(getattr(bar, "class_code", "") or ""),
                    raw={"runtime_index": i},
                )
            )
        out = ensure_timeframe(out, tf, provider="runtime", allow_resample=True)
        if limit > 0 and len(out) > limit:
            out = out[-limit:]
        return out

    async def _ensure_ccxt(self) -> CcxtMarketDataAdapter:
        svc = self.core.service_registry.get("ccxt_market_service")
        live = getattr(svc, "_ccxt", None) if svc is not None else None
        if live is not None and hasattr(live, "fetch_ohlcv"):
            return live  # type: ignore[return-value]
        if self._ccxt is not None:
            return self._ccxt
        cfg = {}
        if svc is not None:
            cfg = dict(getattr(svc, "config", {}) or {})
        if not cfg:
            cfg = dict(await self.core.config.get("services.ccxt_market_service", {}) or {})
        key_env = str(cfg.get("api_key_env") or "CCXT_API_KEY")
        secret_env = str(cfg.get("secret_env") or "CCXT_SECRET")
        password_env = str(cfg.get("password_env") or "CCXT_PASSWORD")
        self._ccxt = CcxtMarketDataAdapter(
            exchange_id=str(cfg.get("exchange") or "binance"),
            sandbox=bool(cfg.get("sandbox", False)),
            api_key=os.environ.get(key_env, ""),
            secret=os.environ.get(secret_env, ""),
            password=os.environ.get(password_env, ""),
        )
        await self._ccxt.connect()
        return self._ccxt

    async def _ensure_alpaca(self) -> AlpacaMarketDataAdapter:
        svc = self.core.service_registry.get("alpaca_market_service")
        live = getattr(svc, "_alpaca", None) if svc is not None else None
        if live is not None and hasattr(live, "fetch_ohlcv"):
            return live  # type: ignore[return-value]
        if self._alpaca is not None:
            return self._alpaca
        cfg = {}
        if svc is not None:
            cfg = dict(getattr(svc, "config", {}) or {})
        if not cfg:
            cfg = dict(await self.core.config.get("services.alpaca_market_service", {}) or {})
        key_env = str(cfg.get("api_key_env") or "ALPACA_API_KEY")
        secret_env = str(cfg.get("secret_key_env") or "ALPACA_SECRET_KEY")
        secret = os.environ.get(secret_env, "") or os.environ.get("ALPACA_API_SECRET_KEY", "")
        self._alpaca = AlpacaMarketDataAdapter(
            api_key=os.environ.get(key_env, "") or os.environ.get("APCA_API_KEY_ID", ""),
            secret_key=secret or os.environ.get("APCA_API_SECRET_KEY", ""),
            paper=bool(cfg.get("paper", True)),
            feed=str(cfg.get("feed") or "iex"),
            asset_class=str(cfg.get("asset_class") or "stock"),
            exchange="alpaca",
        )
        if not self._alpaca.available:
            raise RuntimeError("alpaca adapter unavailable (alpaca-py)")
        await self._alpaca.connect()
        return self._alpaca

    async def publish_bars(
        self,
        bars: list[Candle],
        *,
        class_code: str = "TQBR",
        provider: str | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Publish already-fetched bars as ``market.history.candle``."""
        if not force and not bool(self.config.get("publish_events", True)):
            return [
                candle_to_market_payload(
                    bar,
                    class_code=class_code,
                    source=self._resolve_provider(provider, class_code=class_code),
                    history=True,
                )
                for bar in bars
            ]
        prov = self._resolve_provider(provider, class_code=class_code)
        instance_id = str(
            await self.core.config.get("instance.id", "local") or "local"
        )
        payloads: list[dict[str, Any]] = []
        for bar in bars:
            payload = candle_to_market_payload(
                bar, class_code=class_code, source=prov, history=True
            )
            payloads.append(payload)
            await publish_market_history_candle(
                self.core.event_bus,
                source_id=self.component_id,
                payload=payload,
                instance_id=instance_id,
            )
        return payloads

    async def publish_history(
        self,
        ticker: str,
        *,
        class_code: str = "TQBR",
        provider: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        bars = await self.fetch(
            ticker,
            class_code=class_code,
            provider=provider,
            timeframe=timeframe,
            limit=limit,
            start=start,
            end=end,
        )
        return await self.publish_bars(
            bars, class_code=class_code, provider=provider
        )

    async def replay(
        self,
        ticker: str,
        *,
        class_code: str = "TQBR",
        provider: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
        delay_sec: float | None = None,
        run_id: str | None = None,
        strategy_id: str = "",
        pause_live: bool | None = None,
    ) -> dict[str, Any]:
        """
        Fetch OHLCV and republish each bar as ``market.candle`` (backtest feed).

        Strategies / runtime see the same Event Contract as live/mock.
        Emits ``backtest.run_started`` / ``backtest.run_finished``.
        """
        bars = await self.fetch(
            ticker,
            class_code=class_code,
            provider=provider,
            timeframe=timeframe,
            limit=limit,
            start=start,
            end=end,
        )
        rid = run_id or uuid.uuid4().hex[:12]
        prov = self._resolve_provider(provider, ticker=ticker, class_code=class_code)
        delay = float(
            delay_sec
            if delay_sec is not None
            else self.config.get("replay_delay_sec") or 0.0
        )
        do_pause = (
            bool(self.config.get("replay_pause_live", True))
            if pause_live is None
            else bool(pause_live)
        )
        # Replaying the same CandleStore buffer must not stop the live chart feed.
        if prov in {"runtime", "live", "chart", "engine"}:
            do_pause = False
        instance_id = str(
            await self.core.config.get("instance.id", "local") or "local"
        )

        paused: list[Any] = []
        if do_pause:
            for sid in (
                "mock_market_service",
                "alpaca_market_service",
                "quik_market_service",
                "ccxt_market_service",
            ):
                svc = self.core.service_registry.get(sid)
                if svc is not None and getattr(svc, "is_running", False):
                    try:
                        await svc.stop()
                        paused.append(svc)
                    except Exception:
                        pass

        started = {
            "run_id": rid,
            "ticker": ticker,
            "class_code": class_code,
            "provider": prov,
            "bars": len(bars),
            "strategy_id": strategy_id,
            "mode": "replay",
        }
        await self.core.event_bus.publish(
            KernelEvent(
                event_type="backtest.run_started",
                source_id=self.component_id,
                payload=started,
                instance_id=instance_id,
            )
        )

        published = 0
        try:
            for bar in bars:
                payload = candle_to_market_payload(
                    bar, class_code=class_code, source=f"replay:{prov}", history=False
                )
                payload["source"] = f"replay:{prov}"
                payload["run_id"] = rid
                await publish_market_candle(
                    self.core.event_bus,
                    source_id=self.component_id,
                    payload=payload,
                    instance_id=instance_id,
                )
                published += 1
                if delay > 0:
                    await asyncio.sleep(delay)
        finally:
            for svc in paused:
                try:
                    if svc.config.get("enabled", True):
                        await svc.start()
                except Exception:
                    pass

        finished = {
            "run_id": rid,
            "ticker": ticker,
            "class_code": class_code,
            "provider": prov,
            "bars": published,
            "strategy_id": strategy_id,
            "mode": "replay",
            "summary": self.summarize(bars),
        }
        await self.core.event_bus.publish(
            KernelEvent(
                event_type="backtest.run_finished",
                source_id=self.component_id,
                payload=finished,
                instance_id=instance_id,
            )
        )
        self._last_replay = finished
        return finished

    def summarize(self, bars: list[Candle]) -> dict[str, Any]:
        if not bars:
            return {"bars": 0}
        closes = [b.close for b in bars]
        vols = [b.volume for b in bars]
        last = bars[-1]
        atr_pct = None
        if len(bars) >= 15:
            ranges = [
                max(bars[i].high - bars[i].low, 1e-9) for i in range(-14, 0)
            ]
            atr = sum(ranges) / 14.0
            atr_pct = atr / max(last.close, 1e-9)
        realized_vol = None
        if len(closes) >= 6:
            rets = []
            for i in range(1, len(closes)):
                if closes[i - 1] > 0:
                    rets.append(math.log(closes[i] / closes[i - 1]))
            if len(rets) >= 5:
                mean = sum(rets) / len(rets)
                var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
                realized_vol = math.sqrt(var) * math.sqrt(252.0)
        avg_vol_5 = sum(vols[-5:]) / min(5, len(vols))
        return {
            "bars": len(bars),
            "last_close": last.close,
            "last_date": last.ts.strftime("%Y-%m-%d"),
            "avg_volume": sum(vols) / len(vols),
            "volume": avg_vol_5,
            "dollar_volume_est": last.close * avg_vol_5,
            "atr_pct": atr_pct,
            "realized_vol": realized_vol,
            "return_pct": (closes[-1] / closes[0] - 1.0) if closes[0] else None,
        }

    async def metrics_for(
        self,
        ticker: str,
        *,
        class_code: str = "TQBR",
        provider: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Cheap catalog metrics from recent OHLCV."""
        lim = int(limit or self.config.get("metrics_bars") or 40)
        bars = await self.fetch(
            ticker,
            class_code=class_code,
            provider=provider,
            limit=lim,
        )
        summary = self.summarize(bars)
        if not summary.get("bars"):
            return {"symbol": ticker, "class_code": class_code, "ok": False}
        return {
            "symbol": ticker,
            "class_code": class_code,
            "ok": True,
            "price": float(summary["last_close"] or 0),
            "volume": float(summary.get("volume") or 0),
            "dollar_volume": float(summary.get("dollar_volume_est") or 0),
            "atr_pct": float(summary.get("atr_pct") or 0),
            "realized_vol": float(summary.get("realized_vol") or 0),
            "source": self._resolve_provider(provider, ticker=ticker, class_code=class_code),
            "as_of": summary.get("last_date"),
        }

    async def refresh_instruments(
        self,
        instruments: Iterable[Instrument],
        *,
        provider: str | None = None,
        asset_classes: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Fetch metrics for catalog rows; returns overlay keyed by ``Instrument.key``.

        Skips asset classes not in ``asset_classes`` (default: equity, etf).
        """
        allow = {
            a.lower()
            for a in (
                asset_classes
                if asset_classes is not None
                else ("equity", "etf")
            )
        }
        overlays: dict[str, dict[str, Any]] = {}
        for inst in instruments:
            if allow and inst.asset_class.lower() not in allow:
                continue
            # Yahoo/MOEX history path is equity-board oriented for now
            resolved = self._resolve_provider(
                provider, ticker=inst.symbol, class_code=inst.class_code or ""
            )
            if resolved == "yahoo" and "/" in inst.symbol:
                continue
            try:
                m = await self.metrics_for(
                    inst.symbol,
                    class_code=inst.class_code or "TQBR",
                    provider=provider if provider not in (None, "", "auto") else resolved,
                    limit=limit,
                )
            except Exception as exc:  # noqa: BLE001 — per-symbol resilience
                overlays[inst.key] = {"ok": False, "error": str(exc)}
                continue
            if not m.get("ok"):
                continue
            overlays[inst.key] = {
                "price": m["price"],
                "volume": m["volume"],
                "dollar_volume": m["dollar_volume"],
                "atr_pct": m["atr_pct"],
                "realized_vol": m["realized_vol"],
                "ok": True,
                "source": m.get("source"),
                "as_of": m.get("as_of"),
            }
        return overlays
