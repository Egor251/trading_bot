"""CCXT MarketDataPort — optional dependency."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, AsyncIterator

from engine.instruments import InstrumentSpec
from engine.market import Candle

_QUOTE_SUFFIXES = (
    "USDT",
    "USDC",
    "BUSD",
    "USD",
    "EUR",
    "BTC",
    "ETH",
)


def normalize_ccxt_symbol(symbol: str, *, class_code: str = "") -> str:
    """Map universe keys / compact ids to CCXT spot form (e.g. BTC/USDT)."""
    raw = str(symbol or "").strip()
    if not raw:
        return raw
    if ":" in raw:
        # CRYPTO:BTC/USDT or binance:BTC/USDT
        left, right = raw.split(":", 1)
        if "/" in right or right.isupper():
            raw = right.strip()
        elif left.upper() in {"CRYPTO", "SPOT"} or "/" in left:
            raw = right.strip() if right else left
    raw = raw.replace("-", "/")
    if "/" in raw:
        base, quote = raw.split("/", 1)
        return f"{base.strip().upper()}/{quote.strip().upper()}"
    compact = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    for quote in _QUOTE_SUFFIXES:
        if compact.endswith(quote) and len(compact) > len(quote):
            return f"{compact[: -len(quote)]}/{quote}"
    _ = class_code  # reserved for venue-specific maps
    return compact


def _row_to_candle(
    row: list[Any],
    *,
    symbol: str,
    timeframe: str,
    exchange_id: str,
    class_code: str,
) -> Candle:
    ts_ms, o, h, l, c, v = row[:6]
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        ts=datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC),
        open=float(o),
        high=float(h),
        low=float(l),
        close=float(c),
        volume=float(v),
        exchange=exchange_id,
        class_code=class_code,
        raw={"ohlcv": row},
    )


def _ccxt_increment(value: Any, *, fallback: str) -> Decimal:
    """Normalize CCXT precision/increment fields without binary-float leakage."""
    if value in (None, ""):
        return Decimal(fallback)
    number = Decimal(str(value))
    if number <= 0:
        return Decimal(fallback)
    # Most exchanges expose an increment. Some older CCXT markets expose a
    # decimal-place count instead.
    if number == number.to_integral_value() and number >= 1:
        return Decimal("1").scaleb(-int(number))
    return number


def instrument_spec_from_ccxt_market(
    market: dict[str, Any], *, exchange_id: str
) -> InstrumentSpec:
    """Build an exact canonical spec from a CCXT ``load_markets`` record."""
    precision = market.get("precision") or {}
    limits = market.get("limits") or {}
    amount_min = (limits.get("amount") or {}).get("min")
    symbol = str(market.get("symbol") or market.get("id") or "").upper()
    derivative = bool(
        market.get("contract")
        or market.get("future")
        or market.get("swap")
        or market.get("option")
    )
    asset_class = (
        "option"
        if market.get("option")
        else "future"
        if market.get("future")
        else "swap"
        if market.get("swap")
        else "crypto"
    )
    return InstrumentSpec(
        venue=exchange_id,
        asset_class=asset_class,
        symbol=symbol,
        currency=str(market.get("settle") or market.get("quote") or "").upper(),
        lot_size=_ccxt_increment(
            precision.get("amount") if amount_min in (None, "") else amount_min,
            fallback="0.00000001",
        ),
        tick_size=_ccxt_increment(precision.get("price"), fallback="0.00000001"),
        contract_multiplier=Decimal(str(market.get("contractSize") or "1")),
        class_code="CRYPTO",
        margin_model="derivative" if derivative else "cash",
        calendar="24/7",
        source=f"ccxt:{exchange_id}",
        metadata={
            "market_id": market.get("id"),
            "base": market.get("base"),
            "quote": market.get("quote"),
            "settle": market.get("settle"),
            "active": market.get("active"),
        },
    )


class CcxtMarketDataAdapter:
    """
    Universal exchange adapter via CCXT.

    ``pip install ccxt`` required. Maps venue symbols (e.g. BTC/USDT) to Candle;
    ``class_code`` is carried through for Event Contract (often CRYPTO).
    Public spot OHLCV needs no API keys. Optional ``ccxt.pro`` watch for lower latency.
    """

    def __init__(
        self,
        *,
        exchange_id: str = "binance",
        sandbox: bool = False,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        options: dict[str, Any] | None = None,
    ) -> None:
        self.exchange_id = exchange_id
        self.sandbox = sandbox
        self._creds = {
            "apiKey": api_key,
            "secret": secret,
            "password": password,
        }
        self._options = {"defaultType": "spot", **dict(options or {})}
        self._exchange: Any = None
        self._pro: Any = None
        self._import_error: str | None = None
        self._watch_supported: bool | None = None

    @property
    def available(self) -> bool:
        # Injected fake exchange (tests) does not require the ccxt package.
        if self._exchange is not None:
            return True
        try:
            import ccxt  # noqa: F401

            return True
        except ImportError as exc:
            self._import_error = str(exc)
            return False

    @property
    def watch_available(self) -> bool:
        if self._watch_supported is not None:
            return self._watch_supported
        try:
            import ccxt.pro as ccxtpro  # noqa: F401

            self._watch_supported = hasattr(ccxtpro, self.exchange_id)
        except Exception:
            self._watch_supported = False
        return bool(self._watch_supported)

    async def connect(self) -> None:
        # Allow tests / hosts to inject a fake exchange before start.
        if self._exchange is not None:
            return
        if not self.available:
            raise ImportError(self._import_error or "ccxt not installed")
        import ccxt.async_support as ccxt_async

        exchange_cls = getattr(ccxt_async, self.exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"unknown ccxt exchange: {self.exchange_id}")
        params: dict[str, Any] = {"enableRateLimit": True, **self._creds}
        if self._options:
            params["options"] = dict(self._options)
        self._exchange = exchange_cls(params)
        if self.sandbox and hasattr(self._exchange, "set_sandbox_mode"):
            self._exchange.set_sandbox_mode(True)

    async def _ensure_pro(self) -> Any:
        if self._pro is not None:
            return self._pro
        if not self.watch_available:
            raise RuntimeError("ccxt.pro watch not available")
        import ccxt.pro as ccxtpro

        exchange_cls = getattr(ccxtpro, self.exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"unknown ccxt.pro exchange: {self.exchange_id}")
        params: dict[str, Any] = {"enableRateLimit": True, **self._creds}
        if self._options:
            params["options"] = dict(self._options)
        self._pro = exchange_cls(params)
        if self.sandbox and hasattr(self._pro, "set_sandbox_mode"):
            self._pro.set_sandbox_mode(True)
        return self._pro

    async def disconnect(self) -> None:
        for client in (self._exchange, self._pro):
            if client is None:
                continue
            try:
                await client.close()
            except Exception:
                pass
        self._exchange = None
        self._pro = None

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        limit: int = 200,
        class_code: str = "",
    ) -> list[Candle]:
        if self._exchange is None:
            await self.connect()
        assert self._exchange is not None
        market = normalize_ccxt_symbol(symbol, class_code=class_code)
        raw = await self._exchange.fetch_ohlcv(market, timeframe=timeframe, limit=limit)
        out: list[Candle] = []
        for row in raw:
            out.append(
                _row_to_candle(
                    row,
                    symbol=market,
                    timeframe=timeframe,
                    exchange_id=self.exchange_id,
                    class_code=class_code,
                )
            )
        return out

    async def fetch_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Load exchange metadata required by live preflight."""
        if self._exchange is None:
            await self.connect()
        assert self._exchange is not None
        markets = await self._exchange.load_markets()
        normalized = normalize_ccxt_symbol(symbol)
        market = markets.get(normalized)
        if market is None:
            raise KeyError(f"CCXT market metadata unavailable: {normalized}")
        return instrument_spec_from_ccxt_market(
            dict(market), exchange_id=self.exchange_id
        )

    async def watch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        class_code: str = "",
    ) -> Candle:
        """One websocket update (forming or closed bar). Falls back to REST poll."""
        market = normalize_ccxt_symbol(symbol, class_code=class_code)
        if self.watch_available:
            try:
                pro = await self._ensure_pro()
                rows = await pro.watch_ohlcv(market, timeframe)
                if rows:
                    return _row_to_candle(
                        rows[-1],
                        symbol=market,
                        timeframe=timeframe,
                        exchange_id=self.exchange_id,
                        class_code=class_code,
                    )
            except Exception:
                pass
        bars = await self.fetch_ohlcv(market, timeframe, limit=3, class_code=class_code)
        if not bars:
            raise RuntimeError(f"no ohlcv for {market}")
        return bars[-1]

    async def stream_candles(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        class_code: str = "",
        poll_sec: float = 5.0,
    ) -> AsyncIterator[Candle]:
        market = normalize_ccxt_symbol(symbol, class_code=class_code)
        while True:
            if self.watch_available:
                try:
                    yield await self.watch_ohlcv(
                        market, timeframe, class_code=class_code
                    )
                    continue
                except Exception:
                    pass
            bars = await self.fetch_ohlcv(
                market, timeframe, limit=3, class_code=class_code
            )
            if bars:
                yield bars[-1]
            await asyncio.sleep(max(1.0, float(poll_sec)))
