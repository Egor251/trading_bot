"""Shared follow-universe market adapter service base."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService, KernelEvent

from engine.candle_store import StoredBar
from engine.market import Candle, MarketDataPort, candle_to_market_payload
from host.events import publish_market_candle
from host.logging_setup import get_log

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


def _mkt_log():
    return get_log("Engine", "Market")


class MarketAdapterServiceBase(BaseService):
    """
    Lifecycle + active-book streaming for any ``MarketDataPort``.

    Subclasses set ``self._adapter`` in ``on_initialize`` and may override
    ``_poll_symbol`` if the adapter needs custom fetch logic.
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self._task: asyncio.Task[None] | None = None
        self._adapter: MarketDataPort | None = None
        self._follow_universe = True
        self._active: list[tuple[str, str]] = []  # (class_code, ticker)
        self._default_class_code = "TQBR"
        self._ticks = 0
        self._last_error: str | None = None
        self._seeded: set[str] = set()
        self._seed_task: asyncio.Task[None] | None = None
        self._persist_tick: int = 0
        self._feed_source: str = "none"  # api | disk | mock | none
        self._seeding: bool = False

    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "interval_sec": 2,
            "timeframe": "1m",
            "class_code": "TQBR",
            "tickers": ["SBER"],
            "follow_universe": True,
            "seed_bars": 500,
            # Persist seed / live bars so charts & strategies survive API gaps.
            "disk_cache_dir": "data/ohlc_cache",
            "disk_cache_ttl_sec": 86_400,
            "disk_cache_stale_ok": True,
            "disk_cache_persist_every_ticks": 12,
        }

    async def on_initialize(self) -> None:
        self._follow_universe = bool(self.config.get("follow_universe", True))
        self._default_class_code = str(self.config.get("class_code", "TQBR"))
        fallback = list(self.config.get("tickers") or ["SBER"])
        self._active = [(self._default_class_code, str(t)) for t in fallback]

    async def start(self) -> None:
        if self._task is not None:
            return
        if self._adapter is None:
            self._last_error = "adapter_not_configured"
            return
        # Re-read after boot: tests / Operator often mutate config post-initialize.
        self._follow_universe = bool(self.config.get("follow_universe", True))
        self._default_class_code = str(self.config.get("class_code", "TQBR"))
        if not self._follow_universe:
            fallback = list(self.config.get("tickers") or ["SBER"])
            self._active = [(self._default_class_code, str(t)) for t in fallback]
        self.is_running = True
        await self._adapter.connect()
        self._seeding = True
        try:
            await self._sync_from_universe_service()
            if not self._active:
                await asyncio.sleep(0.3)
                await self._sync_from_universe_service()
            if self._follow_universe:
                await self.core.event_bus.subscribe(
                    "universe.changed", self._on_universe_changed
                )
            await self._publish_seed_history()
        finally:
            self._seeding = False
        self._task = asyncio.create_task(self._stream_loop())

    def _series_key(self, class_code: str, ticker: str) -> str:
        return f"{class_code}:{ticker}" if class_code else ticker

    def _cache_provider(self) -> str:
        ex = str(self.config.get("exchange") or "").strip().lower()
        if ex:
            return f"live:{ex}"
        return f"live:{self.component_id}"

    def _disk_cache_enabled(self) -> bool:
        return bool(self._disk_cache_root())

    def _disk_cache_root(self) -> str:
        """Resolve the cache root through the shared data root.

        A bare ``data/ohlc_cache`` used to resolve against the process CWD, so the
        cache followed however the engine happened to be started.
        """
        from engine.paths import data_dir

        root = str(self.config.get("disk_cache_dir") or "").strip()
        if not root:
            return ""
        candidate = Path(root)
        if candidate.is_absolute():
            return root
        parts = candidate.parts
        if parts and parts[0] == "data":
            return str(data_dir().joinpath(*parts[1:]))
        return root

    def _disk_cache_path(
        self, class_code: str, ticker: str, *, timeframe: str, limit: int
    ):
        from engine.ohlc_disk_cache import cache_file_path

        root = self._disk_cache_root()
        return cache_file_path(
            Path(root),
            provider=self._cache_provider(),
            ticker=ticker,
            class_code=class_code,
            timeframe=timeframe,
            limit=int(limit),
            start=None,
            end=None,
        )

    def _write_disk_bars(
        self,
        class_code: str,
        ticker: str,
        bars: list[Candle],
        *,
        timeframe: str,
        limit: int,
        source: str,
    ) -> None:
        if not self._disk_cache_enabled() or not bars:
            return
        try:
            from engine.ohlc_disk_cache import write_disk_cache

            path = self._disk_cache_path(
                class_code, ticker, timeframe=timeframe, limit=limit
            )
            write_disk_cache(
                path,
                bars,
                meta={
                    "provider": self._cache_provider(),
                    "ticker": ticker,
                    "class_code": class_code,
                    "timeframe": timeframe,
                    "limit": limit,
                    "source": source,
                    "n": len(bars),
                },
            )
        except Exception as exc:
            _mkt_log().warning(
                "{} disk cache write failed {} {} err={}",
                self.component_id,
                class_code,
                ticker,
                exc,
            )

    def _read_disk_bars(
        self,
        class_code: str,
        ticker: str,
        *,
        timeframe: str,
        limit: int,
        allow_stale: bool,
    ) -> list[Candle] | None:
        if not self._disk_cache_enabled():
            return None
        try:
            from engine.ohlc_disk_cache import read_disk_cache

            path = self._disk_cache_path(
                class_code, ticker, timeframe=timeframe, limit=limit
            )
            ttl = float(self.config.get("disk_cache_ttl_sec") or 86_400)
            return read_disk_cache(path, ttl_sec=ttl, allow_stale=allow_stale)
        except Exception as exc:
            _mkt_log().warning(
                "{} disk cache read failed {} {} err={}",
                self.component_id,
                class_code,
                ticker,
                exc,
            )
            return None

    def _store_bars_as_candles(
        self, key: str, *, timeframe: str, class_code: str, ticker: str
    ) -> list[Candle]:
        runtime = getattr(self.core, "trading", None)
        store = getattr(runtime, "candles", None) if runtime is not None else None
        if store is None:
            return []
        dq = getattr(store, "_bars", {}).get(key)
        if not dq:
            return []
        out: list[Candle] = []
        for b in dq:
            ts = b.ts
            if ts is None:
                ts = datetime.now(UTC)
            out.append(
                Candle(
                    symbol=ticker or getattr(b, "symbol", ""),
                    timeframe=timeframe,
                    ts=ts,
                    open=float(b.open),
                    high=float(b.high),
                    low=float(b.low),
                    close=float(b.close),
                    volume=float(b.volume or 0.0),
                    exchange=str(self.config.get("exchange") or ""),
                    class_code=class_code or getattr(b, "class_code", ""),
                )
            )
        return out

    def _maybe_persist_live(
        self, class_code: str, ticker: str, *, timeframe: str, force: bool = False
    ) -> None:
        """Write CandleStore series to disk so next seed can restore without API."""
        every = int(self.config.get("disk_cache_persist_every_ticks") or 12)
        self._persist_tick += 1
        if not force and every > 0 and (self._persist_tick % every) != 0:
            return
        seed_n = int(self.config.get("seed_bars", 500) or 500)
        seed_n = max(50, min(seed_n, 1000))
        key = self._series_key(class_code, ticker)
        bars = self._store_bars_as_candles(
            key, timeframe=timeframe, class_code=class_code, ticker=ticker
        )
        if len(bars) < 2:
            return
        self._write_disk_bars(
            class_code,
            ticker,
            bars,
            timeframe=timeframe,
            limit=seed_n,
            source="live",
        )

    def _candle_to_stored(self, candle: Candle, *, class_code: str) -> StoredBar:
        cc = class_code or candle.class_code or ""
        sym = candle.symbol
        return StoredBar(
            symbol=sym,
            class_code=cc,
            ts=candle.ts,
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=float(candle.volume),
            key=self._series_key(cc, sym),
        )

    async def _publish_seed_history(
        self, books: list[tuple[str, str]] | None = None
    ) -> None:
        """Fetch exchange OHLCV (or disk cache) and load into CandleStore."""
        if self._adapter is None:
            return
        timeframe = str(self.config.get("timeframe", "1m"))
        seed_n = int(self.config.get("seed_bars", 500) or 500)
        seed_n = max(50, min(seed_n, 1000))
        stale_ok = bool(self.config.get("disk_cache_stale_ok", True))
        targets = list(books) if books is not None else list(self._active)
        if not targets:
            targets = [
                (self._default_class_code, str(t))
                for t in (self.config.get("tickers") or ["SBER"])
            ]
        try:
            instance_id = str(
                await self.core.config.get("instance.id", "local") or "local"
            )
        except Exception:
            instance_id = "local"

        runtime = getattr(self.core, "trading", None)
        store = getattr(runtime, "candles", None) if runtime is not None else None

        for class_code, ticker in targets:
            key = self._series_key(class_code, ticker)
            bars: list[Candle] = []
            source = "api"
            try:
                bars = await self._adapter.fetch_ohlcv(
                    ticker, timeframe, limit=seed_n, class_code=class_code
                )
            except Exception as exc:
                self._last_error = str(exc)
                _mkt_log().warning(
                    "{} seed API failed {} {} err={}",
                    self.component_id,
                    class_code,
                    ticker,
                    exc,
                )
                bars = []

            if not bars:
                cached = self._read_disk_bars(
                    class_code,
                    ticker,
                    timeframe=timeframe,
                    limit=seed_n,
                    allow_stale=stale_ok,
                )
                if cached:
                    bars = cached
                    source = "disk"
                    _mkt_log().info(
                        "{} seed from disk {} bars={} tf={}",
                        self.component_id,
                        key,
                        len(bars),
                        timeframe,
                    )
                else:
                    _mkt_log().warning(
                        "{} seed empty {} {} (no API, no disk)",
                        self.component_id,
                        class_code,
                        ticker,
                    )
                    continue

            if source == "api":
                self._write_disk_bars(
                    class_code,
                    ticker,
                    bars,
                    timeframe=timeframe,
                    limit=seed_n,
                    source="seed",
                )

            # Prefer api over disk when any symbol seeded from API this pass.
            if source == "api" or self._feed_source not in {"api", "mock"}:
                self._feed_source = source

            if store is not None:
                stored = [self._candle_to_stored(c, class_code=class_code) for c in bars]
                n = store.load_series(key, stored)
                _mkt_log().info(
                    "{} seeded {} bars={} tf={} via={}",
                    self.component_id,
                    key,
                    n,
                    timeframe,
                    source,
                )
            else:
                for candle in bars:
                    payload = candle_to_market_payload(candle, class_code=class_code)
                    payload["seed"] = True
                    await publish_market_candle(
                        self.core.event_bus,
                        source_id=self.component_id,
                        payload=payload,
                        instance_id=instance_id,
                    )

            last = bars[-1]
            payload = candle_to_market_payload(last, class_code=class_code)
            payload["seed"] = True
            payload["seed_source"] = source
            await publish_market_candle(
                self.core.event_bus,
                source_id=self.component_id,
                payload=payload,
                instance_id=instance_id,
            )
            self._seeded.add(key)

    async def stop(self) -> None:
        self.is_running = False
        if self._seed_task is not None:
            self._seed_task.cancel()
            await asyncio.gather(self._seed_task, return_exceptions=True)
            self._seed_task = None
        if self._follow_universe:
            try:
                await self.core.event_bus.unsubscribe(
                    "universe.changed", self._on_universe_changed
                )
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._adapter is not None:
            try:
                timeframe = str(self.config.get("timeframe", "1m"))
                for class_code, ticker in list(self._active):
                    self._maybe_persist_live(
                        class_code, ticker, timeframe=timeframe, force=True
                    )
            except Exception:
                pass
            try:
                await self._adapter.disconnect()
            except Exception:
                pass

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        status.update(
            {
                "follow_universe": self._follow_universe,
                "active": [f"{cc}:{t}" for cc, t in self._active],
                "ticks_published": self._ticks,
                "adapter": type(self._adapter).__name__ if self._adapter else None,
                "error": self._last_error,
                "seeded": sorted(self._seeded),
                "seed_bars": int(self.config.get("seed_bars", 500) or 500),
                "feed_source": self._feed_source,
            }
        )
        return status

    async def _sync_from_universe_service(self) -> None:
        if not self._follow_universe:
            return
        svc = self.core.service_registry.get("universe_service")
        if svc is None:
            return
        snap = getattr(svc, "last_snapshot", None)
        if snap is None:
            return
        self._apply_active(list(snap.active))

    async def _on_universe_changed(self, event: KernelEvent) -> None:
        rows = event.payload.get("active") or []
        self._apply_active(rows)

    def _apply_active(self, rows: list[Any]) -> None:
        active: list[tuple[str, str]] = []
        for row in rows:
            if isinstance(row, dict):
                sym = str(row.get("symbol") or row.get("ticker") or "").strip()
                cc = str(row.get("class_code") or self._default_class_code)
            else:
                sym = str(getattr(row, "symbol", "")).strip()
                cc = str(getattr(row, "class_code", "") or self._default_class_code)
            if sym:
                active.append((cc, sym))
        if not active:
            return
        self._active = active
        need = [
            (cc, t)
            for cc, t in active
            if self._series_key(cc, t) not in self._seeded
        ]
        # During start() seed is awaited once; skip concurrent re-seed.
        if self._seeding:
            return
        if need and self.is_running and self._adapter is not None:
            if self._seed_task is None or self._seed_task.done():
                self._seed_task = asyncio.create_task(self._publish_seed_history(need))

    async def _stream_loop(self) -> None:
        interval = float(self.config.get("interval_sec", 2))
        timeframe = str(self.config.get("timeframe", "1m"))
        while self.is_running:
            try:
                books = list(self._active)
                if not books:
                    books = [
                        (self._default_class_code, str(t))
                        for t in (self.config.get("tickers") or ["SBER"])
                    ]
                instance_id = str(
                    await self.core.config.get("instance.id", "local") or "local"
                )
                for class_code, ticker in books:
                    candle = await self._poll_symbol(
                        ticker, class_code=class_code, timeframe=timeframe
                    )
                    if candle is None:
                        continue
                    payload = candle_to_market_payload(candle, class_code=class_code)
                    await publish_market_candle(
                        self.core.event_bus,
                        source_id=self.component_id,
                        payload=payload,
                        instance_id=instance_id,
                    )
                    self._maybe_persist_live(
                        class_code, ticker, timeframe=timeframe
                    )
                self._ticks += 1
                self._last_error = None
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
                await asyncio.sleep(interval)

    async def _poll_symbol(
        self,
        ticker: str,
        *,
        class_code: str,
        timeframe: str,
    ) -> Candle | None:
        assert self._adapter is not None
        bars = await self._adapter.fetch_ohlcv(
            ticker, timeframe, limit=3, class_code=class_code
        )
        return bars[-1] if bars else None
