"""Market / broker ports — universal adapters normalize to Event Contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from .execution import ExecutionPort as BrokerExecutionPort
from .order import OrderIntent, OrderResult

#: Re-exported for the market-adapter contract. `ruff --fix` previously removed
#: this import as "unused" and broke `tests/test_universe.py`, which imports
#: ``BrokerExecutionPort`` from here.
__all__ = ["BrokerExecutionPort", "candle_to_market_payload"]


@dataclass(slots=True)
class Candle:
    """Normalized OHLCV bar (venue-agnostic)."""

    symbol: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    exchange: str = ""
    class_code: str = ""
    raw: dict[str, Any] | None = None


@runtime_checkable
class MarketDataPort(Protocol):
    """
    Unified market data interface.

    Adapters (mock / CCXT / QUIK / …) implement this and map venue symbols
    to internal ``class_code`` + ``ticker`` when publishing events.
    """

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        limit: int = 200,
        class_code: str = "",
    ) -> list[Candle]: ...

    def stream_candles(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        class_code: str = "",
    ) -> AsyncIterator[Candle]: ...


@runtime_checkable
class LegacyBrokerExecutionPort(Protocol):
    """Deprecated QUIK-only bridge retained during durable-router migration."""

    async def place_order(self, intent: OrderIntent) -> OrderResult: ...


def timeframe_to_interval_min(timeframe: str) -> int:
    tf = (timeframe or "1m").strip().lower()
    if tf.endswith("m") and tf[:-1].isdigit():
        return max(1, int(tf[:-1]))
    if tf.endswith("h") and tf[:-1].isdigit():
        return max(1, int(tf[:-1]) * 60)
    if tf.endswith("d") and tf[:-1].isdigit():
        return max(1, int(tf[:-1]) * 60 * 24)
    if tf in {"d", "1d", "day"}:
        return 1440
    return 1


def candle_to_market_payload(
    candle: Candle,
    *,
    class_code: str | None = None,
    interval_min: int | None = None,
    source: str | None = None,
    history: bool = False,
) -> dict[str, Any]:
    """Map a normalized Candle to ``market.candle`` / ``market.history.candle`` payload."""
    ts = candle.ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    else:
        ts = ts.astimezone(UTC)
    cc = class_code if class_code is not None else (candle.class_code or "")
    out = {
        "class_code": cc,
        "ticker": candle.symbol,
        "interval_min": (
            interval_min
            if interval_min is not None
            else timeframe_to_interval_min(candle.timeframe)
        ),
        "date": ts.strftime("%Y-%m-%d"),
        "time": ts.strftime("%H:%M:%S"),
        # Exchange timestamp as a number. `DataGapDetector` used to fall back to
        # `time.time()` because only `date`/`time` strings were published, so it
        # measured the health of the *polling loop* rather than of the data:
        # normal network jitter produced a "gap", the ops monitor froze risk, and
        # with no auto-resume the bot stayed halted — 3,536 `risk.reject{stage:
        # halted, reason: market_data_gap}` in one month, 0 orders placed.
        # With `ts` present the detector sees real bar spacing, and
        # "last candle age" becomes computable.
        "ts": ts.timestamp(),
        "open": float(candle.open),
        "high": float(candle.high),
        "low": float(candle.low),
        "close": float(candle.close),
        "volume": float(candle.volume),
        "exchange": candle.exchange or "",
    }
    if history or source:
        out["source"] = source or candle.exchange or "history"
    return out


class NoopBrokerExecution:
    """Paper/dev stub — rejects live routing until a real transport is wired."""

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        from .order import OrderStatus

        return OrderResult(
            client_id=intent.client_id,
            status=OrderStatus.REJECTED,
            reason="noop_broker",
        )
