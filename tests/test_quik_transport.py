"""QUIK transport factory + fake backend tests (no terminal required)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.adapters.quik_adapter import QuikMarketDataAdapter  # noqa: E402
from engine.order import OrderIntent, OrderSide, OrderStatus  # noqa: E402
from engine.quik_broker import QuikBrokerExecution  # noqa: E402
from engine.quik_transports import build_quik_transport  # noqa: E402
from engine.quik_transports.fake import FakeQuikTransport  # noqa: E402


def test_factory_null_and_fake() -> None:
    n = build_quik_transport("null")
    assert hasattr(n, "get_candles")
    f = build_quik_transport("fake", base_prices={"SBER": 280.0})
    assert isinstance(f, FakeQuikTransport)


def test_factory_unknown() -> None:
    try:
        build_quik_transport("nope")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


async def _fake_candles_and_order() -> None:
    t = FakeQuikTransport(base_prices={"SBER": 285.0})
    await t.connect()
    assert await t.is_connected()
    rows = await t.get_candles("TQBR", "SBER", interval=1, count=3)
    assert len(rows) == 3
    assert rows[-1].close > 0

    adapter = QuikMarketDataAdapter(transport=t)
    bars = await adapter.fetch_ohlcv("SBER", "1m", limit=2, class_code="TQBR")
    assert len(bars) == 2
    assert bars[0].exchange == "quik"

    broker = QuikBrokerExecution(t, class_code="TQBR")
    result = await broker.place_order(
        OrderIntent(symbol="SBER", side=OrderSide.BUY, quantity=10, limit_price=285.0)
    )
    assert result.status == OrderStatus.FILLED
    assert len(t.transactions) == 1
    await t.close()


def test_fake_path() -> None:
    asyncio.run(_fake_candles_and_order())


def test_quik_python_optional() -> None:
    from engine.quik_transports.quik_python_backend import QuikPythonTransport

    tr = QuikPythonTransport()
    # may or may not be installed
    _ = tr.available


def test_qlua_optional() -> None:
    from engine.quik_transports.qlua_rpc_backend import QluaRpcTransport

    tr = QluaRpcTransport()
    _ = tr.available


if __name__ == "__main__":
    test_factory_null_and_fake()
    test_factory_unknown()
    test_fake_path()
    test_quik_python_optional()
    test_qlua_optional()
    print("quik transport OK")
