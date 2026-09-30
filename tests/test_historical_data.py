"""Tests for historical adapters + service helpers (no live network required)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.adapters.moex_adapter import MoexMarketDataAdapter  # noqa: E402
from engine.adapters.yahoo_adapter import YahooMarketDataAdapter  # noqa: E402
from engine.market import Candle, candle_to_market_payload  # noqa: E402
from host.events import publish_market_history_candle  # noqa: E402
from plugins.market.historical_data_service import HistoricalDataService  # noqa: E402


def test_history_payload_flag() -> None:
    c = Candle(
        symbol="SBER",
        timeframe="1d",
        ts=datetime(2026, 1, 15, tzinfo=UTC),
        open=280,
        high=285,
        low=278,
        close=282,
        volume=1e6,
        exchange="yahoo",
        class_code="TQBR",
    )
    payload = candle_to_market_payload(c, source="yahoo", history=True)
    assert payload["ticker"] == "SBER"
    assert payload["source"] == "yahoo"
    assert payload["date"] == "2026-01-15"


def test_adapters_optional_available() -> None:
    y = YahooMarketDataAdapter()
    m = MoexMarketDataAdapter()
    # presence depends on env; just ensure property does not raise
    _ = y.available
    _ = m.available


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


class _FakeConfig:
    async def get(self, _key: str, default: Any = None) -> Any:
        return default


class _FakeCore:
    def __init__(self) -> None:
        self.event_bus = _FakeBus()
        self.config = _FakeConfig()


class _StubYahoo:
    available = True

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", **kwargs: Any) -> list[Candle]:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        out = []
        for i in range(20):
            px = 100.0 + i
            out.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=base + timedelta(days=i),
                    open=px,
                    high=px + 1,
                    low=px - 1,
                    close=px + 0.5,
                    volume=1000 + i,
                    exchange="yahoo",
                    class_code=kwargs.get("class_code") or "TQBR",
                )
            )
        limit = int(kwargs.get("limit") or 20)
        return out[-limit:]


async def _service_fetch_publish() -> None:
    core = _FakeCore()
    svc = HistoricalDataService(core, "historical_data_service")  # type: ignore[arg-type]
    svc.config = svc.default_config()
    svc._yahoo = _StubYahoo()  # noqa: SLF001
    svc._moex = None  # noqa: SLF001

    bars = await svc.fetch("SBER", class_code="TQBR", limit=10)
    assert len(bars) == 10
    assert bars[0].symbol == "SBER"  # stripped .ME

    summary = svc.summarize(bars)
    assert summary["bars"] == 10
    assert summary["last_close"] is not None

    payloads = await svc.publish_bars(bars, class_code="TQBR", provider="yahoo", force=True)
    assert len(payloads) == 10
    assert len(core.event_bus.events) == 10
    assert core.event_bus.events[0].event_type == "market.history.candle"


def test_historical_service_stubbed() -> None:
    asyncio.run(_service_fetch_publish())


async def _publish_helper() -> None:
    bus = _FakeBus()
    payload = {
        "class_code": "TQBR",
        "ticker": "SBER",
        "interval_min": 1440,
        "date": "2026-01-15",
        "time": "00:00:00",
        "open": 1,
        "high": 2,
        "low": 0.5,
        "close": 1.5,
        "volume": 10,
        "source": "yahoo",
    }
    ev = await publish_market_history_candle(bus, source_id="test", payload=payload)
    assert ev.event_type == "market.history.candle"
    assert bus.events[0].payload["ticker"] == "SBER"


def test_publish_market_history_candle() -> None:
    asyncio.run(_publish_helper())


async def _cache_and_metrics() -> None:
    core = _FakeCore()
    svc = HistoricalDataService(core, "historical_data_service")  # type: ignore[arg-type]
    svc.config = svc.default_config()
    svc.config["cache_ttl_sec"] = 60
    svc.config["min_fetch_interval_sec"] = 0
    # Isolate memory-cache assertions from shared on-disk OHLC cache.
    svc.config["disk_cache_ttl_sec"] = 0
    svc.config["disk_cache_dir"] = ""
    svc._yahoo = _StubYahoo()  # noqa: SLF001
    svc._moex = None  # noqa: SLF001

    bars1 = await svc.fetch("SBER", limit=10)
    assert svc._last_fetch.get("cache") is False  # noqa: SLF001
    bars2 = await svc.fetch("SBER", limit=10)
    assert svc._last_fetch.get("cache") in {True, "memory"}  # noqa: SLF001
    assert len(bars1) == len(bars2)

    m = await svc.metrics_for("SBER", class_code="TQBR", limit=10)
    assert m["ok"] is True
    assert m["price"] > 0
    assert m["dollar_volume"] > 0

    from engine.universe import Instrument

    overlays = await svc.refresh_instruments(
        [
            Instrument("SBER", "TQBR", "equity"),
            Instrument("BTC/USDT", "CRYPTO", "crypto"),
        ],
        asset_classes=["equity"],
    )
    assert "TQBR:SBER" in overlays
    assert overlays["TQBR:SBER"]["ok"] is True
    assert "CRYPTO:BTC/USDT" not in overlays


def test_cache_and_metrics() -> None:
    asyncio.run(_cache_and_metrics())


async def _universe_overlay() -> None:
    from services.universe_service import UniverseService

    class _Hist:
        async def refresh_instruments(self, instruments, **kwargs):
            return {
                "TQBR:SBER": {
                    "ok": True,
                    "price": 300.0,
                    "volume": 1e6,
                    "dollar_volume": 3e8,
                    "atr_pct": 0.02,
                    "realized_vol": 0.3,
                }
            }

    class _Reg:
        def get(self, name: str):
            return _Hist() if name == "historical_data_service" else None

    class _Core2(_FakeCore):
        def __init__(self) -> None:
            super().__init__()
            self.service_registry = _Reg()

    core = _Core2()
    uni = UniverseService(core, "universe_service")  # type: ignore[arg-type]
    uni.config = uni.default_config()
    uni.config["catalog_path"] = ""
    uni.config["catalog"] = [
        {
            "symbol": "SBER",
            "class_code": "TQBR",
            "asset_class": "equity",
            "price": 100.0,
            "dollar_volume": 1.0,
        }
    ]
    stats = await uni.refresh_catalog_metrics(force=True)
    assert stats["updated"] == 1
    assert len(uni._catalog) == 1  # noqa: SLF001
    assert uni._catalog[0].price == 300.0  # noqa: SLF001
    assert uni._catalog[0].dollar_volume == 3e8  # noqa: SLF001


def test_universe_metric_overlay() -> None:
    asyncio.run(_universe_overlay())


async def _replay_publishes_candles() -> None:
    core = _FakeCore()

    class _Reg:
        def get(self, _name: str):
            return None

    core.service_registry = _Reg()  # type: ignore[attr-defined]
    svc = HistoricalDataService(core, "historical_data_service")  # type: ignore[arg-type]
    svc.config = svc.default_config()
    svc.config["replay_pause_live"] = True
    svc.config["replay_delay_sec"] = 0
    svc._yahoo = _StubYahoo()  # noqa: SLF001
    svc._moex = None  # noqa: SLF001

    candles: list[str] = []
    lifecycle: list[str] = []

    async def on_any(event) -> None:
        if event.event_type == "market.candle":
            candles.append(event.payload.get("ticker", ""))
        elif event.event_type.startswith("backtest."):
            lifecycle.append(event.event_type)

    # Fake bus already collects; monkeypatch publish to also track types
    orig_publish = core.event_bus.publish

    async def _pub(event):
        await on_any(event)
        await orig_publish(event)

    core.event_bus.publish = _pub  # type: ignore[method-assign]

    result = await svc.replay("SBER", class_code="TQBR", limit=10)
    assert result["bars"] == 10
    assert len(candles) == 10
    assert "backtest.run_started" in lifecycle
    assert "backtest.run_finished" in lifecycle
    assert any(
        e.event_type == "market.candle" and e.payload.get("source", "").startswith("replay:")
        for e in core.event_bus.events
    )


def test_replay_as_market_candle() -> None:
    asyncio.run(_replay_publishes_candles())


if __name__ == "__main__":
    test_history_payload_flag()
    test_adapters_optional_available()
    test_historical_service_stubbed()
    test_publish_market_history_candle()
    test_cache_and_metrics()
    test_universe_metric_overlay()
    test_replay_as_market_candle()
    print("historical data OK")
