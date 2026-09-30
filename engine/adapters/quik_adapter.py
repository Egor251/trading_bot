"""QUIK market adapter over QuikTransport (no QuikPy import)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, AsyncIterator

from engine.instruments import InstrumentSpec
from engine.market import Candle
from engine.quik_transport import QuikTransport, UnavailableQuikTransport


def instrument_spec_from_quik_security(
    security: dict[str, Any], *, class_code: str, symbol: str
) -> InstrumentSpec:
    """Translate QUIK ``getSecurityInfo`` fields into economic invariants."""
    cc = str(class_code or security.get("class_code") or "").upper()
    if "OPT" in cc:
        asset_class, margin_model = "option", "derivative"
    elif "FUT" in cc:
        asset_class, margin_model = "future", "derivative"
    else:
        asset_class, margin_model = "equity", "cash"
    tick_size = Decimal(str(security.get("min_price_step") or "0"))
    tick_value = Decimal(
        str(security.get("step_price") or security.get("step_price_currency") or "0")
    )
    multiplier = (
        tick_value / tick_size
        if tick_size > 0 and tick_value > 0 and margin_model == "derivative"
        else Decimal(str(security.get("face_value") or "1"))
    )
    return InstrumentSpec(
        venue="MOEX",
        asset_class=asset_class,
        symbol=symbol,
        currency=str(
            security.get("currency")
            or security.get("base_active_classcode")
            or "RUB"
        ),
        lot_size=Decimal(str(security.get("lot_size") or security.get("lot") or "0")),
        tick_size=tick_size,
        tick_value=tick_value if tick_value > 0 else None,
        contract_multiplier=multiplier,
        class_code=cc,
        margin_model=margin_model,
        calendar="MOEX",
        source="quik:getSecurityInfo",
        metadata={
            "short_name": security.get("short_name"),
            "scale": security.get("scale"),
            "maturity_date": security.get("mat_date"),
        },
    )


class QuikMarketDataAdapter:
    """MarketDataPort backed by a swappable ``QuikTransport``."""

    def __init__(self, transport: QuikTransport | None = None) -> None:
        self.transport: QuikTransport = transport or UnavailableQuikTransport()
        self._connected = False

    async def connect(self) -> None:
        await self.transport.connect()
        self._connected = True

    async def disconnect(self) -> None:
        await self.transport.close()
        self._connected = False

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        limit: int = 200,
        class_code: str = "",
    ) -> list[Candle]:
        from engine.market import timeframe_to_interval_min

        interval = timeframe_to_interval_min(timeframe)
        rows = await self.transport.get_candles(
            class_code or "TQBR",
            symbol,
            interval=interval,
            count=max(1, int(limit)),
        )
        out: list[Candle] = []
        for row in rows:
            ts = datetime.now(UTC)
            if row.datetime:
                try:
                    ts = datetime(
                        int(row.datetime.get("year", ts.year)),
                        int(row.datetime.get("month", ts.month)),
                        int(row.datetime.get("day", ts.day)),
                        int(row.datetime.get("hour", 0)),
                        int(row.datetime.get("min", row.datetime.get("minute", 0))),
                        tzinfo=UTC,
                    )
                except (TypeError, ValueError):
                    pass
            out.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=ts,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=float(row.volume),
                    exchange="quik",
                    class_code=class_code or row.class_code,
                    raw=row.raw,
                )
            )
        return out

    async def fetch_instrument_spec(
        self, symbol: str, *, class_code: str = "TQBR"
    ) -> InstrumentSpec:
        """Fetch authoritative QUIK metadata; fail if transport lacks the call."""
        getter = getattr(self.transport, "get_security_info", None)
        if getter is None:
            raise RuntimeError("QUIK transport does not expose get_security_info")
        raw = await getter(class_code, symbol)
        if not isinstance(raw, dict):
            raise RuntimeError(f"invalid QUIK security metadata for {class_code}:{symbol}")
        return instrument_spec_from_quik_security(
            raw, class_code=class_code, symbol=symbol
        )

    async def stream_candles(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        class_code: str = "",
    ) -> AsyncIterator[Candle]:
        while True:
            bars = await self.fetch_ohlcv(
                symbol, timeframe, limit=1, class_code=class_code
            )
            if bars:
                yield bars[-1]
