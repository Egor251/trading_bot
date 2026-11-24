"""CCXT market service — universal exchange feed on active universe."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from engine.adapters import MockMarketDataAdapter
from engine.adapters.ccxt_adapter import CcxtMarketDataAdapter, normalize_ccxt_symbol
from engine.market import Candle, candle_to_market_payload, closed_bar_arrived
from host.events import publish_market_candle
from host.logging_setup import get_log
from plugins.market.market_adapter_base import MarketAdapterServiceBase

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


def _svc_log():
    return get_log("Engine", "CcxtMarket")


def _looks_like_ccxt_symbol(symbol: str, class_code: str) -> bool:
    cc = str(class_code or "").upper()
    if cc in {"CRYPTO", "SPOT", "BINANCE", "BITGET", "BYBIT", "OKX", "KRAKEN"}:
        return True
    if cc in {"US", "TQBR", "SPBX", "FORTS"}:
        return False
    sym = str(symbol or "")
    if "/" in sym or "-" in sym:
        return True
    compact = sym.replace("/", "").replace("-", "").upper()
    return any(
        compact.endswith(q) and len(compact) > len(q) for q in ("USDT", "USDC", "BTC", "ETH")
    )


# Exchange-specific credential env defaults (override via config api_key_env etc.).
_EXCHANGE_CRED_ENVS: dict[str, tuple[str, str, str]] = {
    "bitget": ("BITGET_API_KEY", "BITGET_SECRET", "BITGET_PASSWORD"),
    "binance": ("CCXT_API_KEY", "CCXT_SECRET", "CCXT_PASSWORD"),
    "bybit": ("CCXT_API_KEY", "CCXT_SECRET", "CCXT_PASSWORD"),
    "okx": ("CCXT_API_KEY", "CCXT_SECRET", "CCXT_PASSWORD"),
}


def _resolve_ccxt_cred_envs(exchange: str, config: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve (api_key_env, secret_env, password_env) for an exchange."""
    ex = str(exchange or "").strip().lower()
    defaults = _EXCHANGE_CRED_ENVS.get(ex, ("CCXT_API_KEY", "CCXT_SECRET", "CCXT_PASSWORD"))
    return (
        str(config.get("api_key_env") or defaults[0]),
        str(config.get("secret_env") or defaults[1]),
        str(config.get("password_env") or defaults[2]),
    )


class CcxtMarketService(MarketAdapterServiceBase):
    """
    Polls (or watches) OHLCV via CCXT for ``universe.active`` (or config tickers).

    Public spot OHLCV needs no keys. Bitget / OKX passphrase → ``password_env``.
    Requires ``pip install ccxt``.
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self._ccxt: CcxtMarketDataAdapter | None = None
        self._watch_tasks: list[asyncio.Task[None]] = []

    def default_config(self) -> dict[str, Any]:
        cfg = super().default_config()
        cfg.update(
            {
                "enabled": False,
                "auto_start": False,
                "interval_sec": 5,
                "timeframe": "1m",
                "class_code": "CRYPTO",
                "tickers": ["BTC/USDT", "ETH/USDT"],
                "follow_universe": True,
                "exchange": "bitget",
                "sandbox": False,
                "seed_bars": 500,
                "use_watch": True,
                "fallback_mock": False,
                "disk_cache_dir": "data/ohlc_cache",
                "disk_cache_ttl_sec": 86_400,
                "disk_cache_stale_ok": True,
                "disk_cache_persist_every_ticks": 12,
                "api_key_env": "BITGET_API_KEY",
                "secret_env": "BITGET_SECRET",
                "password_env": "BITGET_PASSWORD",
            }
        )
        return cfg

    def _mock_fallback_allowed(self) -> bool:
        if str(os.environ.get("TRADING_ALLOW_MOCK") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return True
        return bool(self.config.get("fallback_mock", False))

    def _build_ccxt_adapter(self) -> CcxtMarketDataAdapter:
        exchange = str(self.config.get("exchange") or "bitget").strip().lower()
        key_env, secret_env, password_env = _resolve_ccxt_cred_envs(exchange, self.config)
        return CcxtMarketDataAdapter(
            exchange_id=exchange,
            sandbox=bool(self.config.get("sandbox", False)),
            api_key=os.environ.get(key_env, ""),
            secret=os.environ.get(secret_env, ""),
            password=os.environ.get(password_env, ""),
        )

    async def on_initialize(self) -> None:
        await super().on_initialize()
        if not self.config.get("class_code"):
            self._default_class_code = "CRYPTO"
        self._ccxt = self._build_ccxt_adapter()
        self._adapter = self._ccxt

    async def reload_credentials(self) -> dict[str, Any]:
        """Rebuild CCXT client from current env and re-seed active symbols."""
        was_running = self.is_running
        if was_running:
            await self.stop()
        self._ccxt = self._build_ccxt_adapter()
        self._adapter = self._ccxt
        self._seeded.clear()
        self._feed_source = "none"
        if was_running or bool(self.config.get("auto_start", True)):
            await self.start()
        return {
            "reloaded": True,
            "exchange": self.config.get("exchange"),
            "feed_source": self._feed_source,
            "ccxt_available": bool(self._ccxt and self._ccxt.available),
        }

    def _activate_mock_fallback(self, reason: str) -> None:
        """Keep charts/strategies alive when CCXT package or venue is unavailable."""
        runtime = getattr(self.core, "trading", None)
        if str(getattr(runtime, "mode", "paper") or "paper").lower() in {
            "live",
            "quik",
        }:
            if runtime is not None:
                runtime.halt(f"live_market_feed_unavailable:{reason}")
            raise RuntimeError(f"mock market fallback forbidden in live mode: {reason}")
        if not self._mock_fallback_allowed():
            self._last_error = reason
            self._feed_source = "none"
            raise RuntimeError(
                f"ccxt unavailable ({reason}); set fallback_mock=true or "
                "TRADING_ALLOW_MOCK=1 to use synthetic OHLCV"
            )
        prices = {
            "BTC/USDT": 65000.0,
            "ETH/USDT": 3200.0,
            "SOL/USDT": 150.0,
        }
        for _cc, ticker in self._active:
            prices.setdefault(str(ticker), 100.0)
        for ticker in self.config.get("tickers") or []:
            prices.setdefault(str(ticker), 100.0)
        seed_n = int(self.config.get("seed_bars", 500) or 500)
        mock = MockMarketDataAdapter(
            base_prices=prices,
            exchange="mock-ccxt-fallback",
            seed_bars=min(seed_n, 400),
        )
        self._adapter = mock
        self._ccxt = None
        self._last_error = reason
        self._feed_source = "mock"
        self.config["use_watch"] = False
        _svc_log().warning(
            "ccxt unavailable ({}); using mock OHLCV fallback symbols={}",
            reason,
            sorted(prices),
        )

    async def start(self) -> None:
        if self._ccxt is not None and not self._ccxt.available:
            reason = "ccxt_not_installed"
            if self._mock_fallback_allowed():
                self._activate_mock_fallback(reason)
            else:
                self._last_error = reason
                _svc_log().error("ccxt_market_service disabled: {} (pip install ccxt)", reason)
                return
        try:
            await super().start()
        except Exception as exc:
            if self._mock_fallback_allowed() and not isinstance(
                self._adapter, MockMarketDataAdapter
            ):
                _svc_log().warning("ccxt start failed {}; falling back to mock", exc)
                self._activate_mock_fallback(f"ccxt_start_failed:{exc}")
                await super().start()
            else:
                raise

    async def stop(self) -> None:
        await self._cancel_watch_tasks()
        await super().stop()

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        status["exchange"] = self.config.get("exchange")
        status["ccxt_available"] = bool(self._ccxt and self._ccxt.available)
        status["watch_available"] = bool(self._ccxt and self._ccxt.watch_available)
        status["use_watch"] = bool(self.config.get("use_watch", True))
        status["watch_tasks"] = len(self._watch_tasks)
        status["feed_source"] = self._feed_source
        status["fallback_mock"] = self._mock_fallback_allowed()
        return status

    def _apply_active(self, rows: list[Any]) -> None:
        active: list[tuple[str, str]] = []
        for row in rows:
            if isinstance(row, dict):
                sym = str(row.get("symbol") or row.get("ticker") or "").strip()
                cc = str(row.get("class_code") or self._default_class_code)
            else:
                sym = str(getattr(row, "symbol", "")).strip()
                cc = str(getattr(row, "class_code", "") or self._default_class_code)
            if not sym:
                continue
            if not _looks_like_ccxt_symbol(sym, cc):
                continue
            active.append((cc or "CRYPTO", normalize_ccxt_symbol(sym, class_code=cc)))
        if not active:
            return
        self._active = active
        need = [(cc, t) for cc, t in active if self._series_key(cc, t) not in self._seeded]
        if self._seeding:
            return
        if need and self.is_running and self._adapter is not None:
            if self._seed_task is None or self._seed_task.done():
                self._seed_task = asyncio.create_task(self._publish_seed_history(need))
        if self._task is not None and bool(self.config.get("use_watch", True)):
            asyncio.create_task(self._restart_watch_tasks())

    async def _poll_symbol(
        self,
        ticker: str,
        *,
        class_code: str,
        timeframe: str,
    ) -> Candle | None:
        assert self._adapter is not None
        bars = await self._adapter.fetch_ohlcv(ticker, timeframe, limit=3, class_code=class_code)
        return bars[-1] if bars else None

    async def _stream_loop(self) -> None:
        use_watch = bool(self.config.get("use_watch", True)) and bool(
            self._ccxt and self._ccxt.watch_available
        )
        if use_watch:
            await self._restart_watch_tasks()
            interval = float(self.config.get("interval_sec", 5))
            while self.is_running:
                try:
                    if not self._watch_tasks or any(t.done() for t in self._watch_tasks):
                        await self._restart_watch_tasks()
                    await asyncio.sleep(max(2.0, interval))
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    self._last_error = str(exc)
                    await asyncio.sleep(max(2.0, interval))
            await self._cancel_watch_tasks()
            return
        await super()._stream_loop()

    async def _restart_watch_tasks(self) -> None:
        await self._cancel_watch_tasks()
        if self._ccxt is None or not self.is_running:
            return
        timeframe = str(self.config.get("timeframe", "1m"))
        books = list(self._active) or [
            (self._default_class_code, str(t)) for t in (self.config.get("tickers") or ["BTC/USDT"])
        ]
        for class_code, ticker in books:
            self._watch_tasks.append(
                asyncio.create_task(
                    self._watch_one(ticker, class_code=class_code, timeframe=timeframe)
                )
            )

    async def _cancel_watch_tasks(self) -> None:
        tasks = list(self._watch_tasks)
        self._watch_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _watch_one(
        self,
        ticker: str,
        *,
        class_code: str,
        timeframe: str,
    ) -> None:
        assert self._ccxt is not None
        poll_fallback = float(self.config.get("interval_sec", 5))
        # A websocket update can be the SAME bar re-emitted many times a
        # minute. Publish the previous bar only when the next one opens, so
        # strategies act on a closed close instead of flipping on a forming
        # price. The first update has no predecessor and is held.
        held: Candle | None = None
        while self.is_running:
            try:
                candle = await self._ccxt.watch_ohlcv(ticker, timeframe, class_code=class_code)
                if closed_bar_arrived(held, candle):
                    assert held is not None
                    instance_id = str(await self.core.config.get("instance.id", "local") or "local")
                    payload = candle_to_market_payload(held, class_code=class_code)
                    await publish_market_candle(
                        self.core.event_bus,
                        source_id=self.component_id,
                        payload=payload,
                        instance_id=instance_id,
                    )
                    self._ticks += 1
                    self._last_error = None
                    self._maybe_persist_live(class_code, ticker, timeframe=timeframe)
                held = candle
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
                await asyncio.sleep(max(1.0, poll_fallback))
