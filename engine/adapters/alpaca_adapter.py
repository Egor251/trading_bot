"""Alpaca MarketDataPort — real US equity/crypto bars via alpaca-py."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, AsyncIterator

from engine.instruments import InstrumentSpec
from engine.market import Candle


def _parse_timeframe(timeframe: str) -> Any:
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    tf = (timeframe or "1m").strip().lower()
    if tf in {"1m", "m", "min"}:
        return TimeFrame.Minute
    if tf.endswith("m") and tf[:-1].isdigit():
        return TimeFrame(int(tf[:-1]), TimeFrameUnit.Minute)
    if tf in {"1h", "h", "hour"}:
        return TimeFrame.Hour
    if tf.endswith("h") and tf[:-1].isdigit():
        return TimeFrame(int(tf[:-1]), TimeFrameUnit.Hour)
    if tf in {"1d", "d", "day"}:
        return TimeFrame.Day
    return TimeFrame.Minute


def instrument_spec_from_alpaca_asset(asset: Any) -> InstrumentSpec:
    """Translate an alpaca-py Asset (or mapping-like fake) into a canonical spec."""
    get = (
        asset.get
        if isinstance(asset, dict)
        else lambda name, default=None: getattr(asset, name, default)
    )
    symbol = str(get("symbol") or "").upper()
    asset_class = str(get("asset_class") or "us_equity").lower()
    crypto = "crypto" in asset_class
    fractionable = bool(get("fractionable", crypto))
    lot = (
        get("min_trade_increment")
        or get("min_order_size")
        or ("0.000001" if fractionable else "1")
    )
    tick = get("price_increment") or ("0.00000001" if crypto else "0.01")
    return InstrumentSpec(
        venue=str(get("exchange") or "ALPACA"),
        asset_class="crypto" if crypto else "equity",
        symbol=symbol,
        currency=str(get("currency") or "USD"),
        lot_size=Decimal(str(lot)),
        tick_size=Decimal(str(tick)),
        contract_multiplier=Decimal("1"),
        class_code="CRYPTO" if crypto else "US",
        margin_model="cash",
        calendar="24/7" if crypto else "XNYS",
        source="alpaca_assets",
        metadata={
            "asset_id": str(get("id") or ""),
            "status": str(get("status") or ""),
            "tradable": bool(get("tradable", False)),
            "fractionable": fractionable,
            "shortable": bool(get("shortable", False)),
        },
    )


class AlpacaMarketDataAdapter:
    """
    Stock (and optional crypto) OHLCV from Alpaca Market Data API.

    Uses **alpaca-py** (current SDK). The repo
    https://github.com/alpacahq/alpaca-trade-api-python is deprecated by Alpaca.

    Env / ctor: ``api_key`` + ``secret_key``. Paper keys work for market data.
    Free stock feed: IEX.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        secret_key: str = "",
        paper: bool = True,
        feed: str = "iex",
        asset_class: str = "stock",  # stock | crypto
        exchange: str = "alpaca",
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.feed = (feed or "iex").lower()
        self.asset_class = (asset_class or "stock").lower()
        self.exchange = exchange
        self._stock_client: Any = None
        self._crypto_client: Any = None
        self._trading_client: Any = None
        self._import_error: str | None = None
        self._last_error: str | None = None

    @property
    def available(self) -> bool:
        try:
            import alpaca.data.historical  # noqa: F401

            return True
        except ImportError as exc:
            self._import_error = str(exc)
            return False

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    async def connect(self) -> None:
        if not self.available:
            raise ImportError(
                self._import_error
                or "alpaca-py not installed — pip install alpaca-py"
            )
        if not self.configured:
            raise RuntimeError(
                "Alpaca keys missing — set ALPACA_API_KEY and ALPACA_SECRET_KEY"
            )
        from alpaca.data.historical import (
            CryptoHistoricalDataClient,
            StockHistoricalDataClient,
        )

        # Market data clients use the same keys for paper/live data API
        self._stock_client = StockHistoricalDataClient(self.api_key, self.secret_key)
        self._crypto_client = CryptoHistoricalDataClient(self.api_key, self.secret_key)

    async def disconnect(self) -> None:
        self._stock_client = None
        self._crypto_client = None
        self._trading_client = None

    async def fetch_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Fetch authoritative Alpaca asset metadata for live validation."""
        if not self.configured:
            raise RuntimeError(
                "Alpaca keys missing — set ALPACA_API_KEY and ALPACA_SECRET_KEY"
            )
        if self._trading_client is None:
            from alpaca.trading.client import TradingClient

            self._trading_client = TradingClient(
                self.api_key, self.secret_key, paper=self.paper
            )
        asset = await asyncio.to_thread(self._trading_client.get_asset, symbol)
        return instrument_spec_from_alpaca_asset(asset)

    def _feed_enum(self) -> Any:
        from alpaca.data.enums import DataFeed

        name = self.feed.upper()
        return getattr(DataFeed, name, DataFeed.IEX)

    def _fetch_sync(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int,
        class_code: str,
    ) -> list[Candle]:
        from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest

        tf = _parse_timeframe(timeframe)
        # Ask Alpaca for enough bars, then slice to ``limit``
        api_limit = max(int(limit), 30)
        minutes = 1
        tfl = (timeframe or "1m").lower()
        if tfl.endswith("m") and tfl[:-1].isdigit():
            minutes = max(1, int(tfl[:-1]))
        elif tfl.endswith("h") and tfl[:-1].isdigit():
            minutes = max(1, int(tfl[:-1]) * 60)
        elif tfl in {"1d", "d", "day"}:
            minutes = 1440
        lookback = timedelta(minutes=max(minutes * api_limit * 4, minutes * 400))
        end = datetime.now(UTC)
        start = end - lookback

        if self.asset_class == "crypto":
            assert self._crypto_client is not None
            # Alpaca crypto symbols often BTC/USD
            sym = symbol if "/" in symbol else f"{symbol}/USD"
            req = CryptoBarsRequest(
                symbol_or_symbols=sym,
                timeframe=tf,
                start=start,
                end=end,
                limit=api_limit,
            )
            barset = self._crypto_client.get_crypto_bars(req)
            raw_bars = barset.data.get(sym) or barset.data.get(symbol) or []
        else:
            assert self._stock_client is not None
            sym = symbol.upper().replace("US:", "").split(":")[-1]
            req = StockBarsRequest(
                symbol_or_symbols=sym,
                timeframe=tf,
                start=start,
                end=end,
                limit=api_limit,
                feed=self._feed_enum(),
            )
            barset = self._stock_client.get_stock_bars(req)
            raw_bars = []
            if hasattr(barset, "data") and isinstance(barset.data, dict):
                raw_bars = barset.data.get(sym) or []
            elif hasattr(barset, "__getitem__"):
                try:
                    raw_bars = list(barset[sym])
                except Exception:
                    raw_bars = []

        out: list[Candle] = []
        for bar in raw_bars:
            ts = getattr(bar, "timestamp", None) or getattr(bar, "t", None)
            if ts is None:
                continue
            if getattr(ts, "tzinfo", None) is None:
                ts = ts.replace(tzinfo=UTC)
            else:
                ts = ts.astimezone(UTC)
            out.append(
                Candle(
                    symbol=symbol.split(":")[-1] if ":" in symbol else symbol,
                    timeframe=timeframe,
                    ts=ts,
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=float(getattr(bar, "volume", 0) or 0),
                    exchange=self.exchange,
                    class_code=class_code,
                    raw={"alpaca": True, "vwap": getattr(bar, "vwap", None)},
                )
            )
        return out[-max(1, int(limit)) :]

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        limit: int = 200,
        class_code: str = "",
    ) -> list[Candle]:
        if self._stock_client is None and self._crypto_client is None:
            await self.connect()
        try:
            bars = await asyncio.to_thread(
                self._fetch_sync,
                symbol,
                timeframe,
                limit=max(1, int(limit)),
                class_code=class_code,
            )
            self._last_error = None
            return bars
        except Exception as exc:
            self._last_error = str(exc)
            raise

    async def stream_candles(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        class_code: str = "",
    ) -> AsyncIterator[Candle]:
        while True:
            bars = await self.fetch_ohlcv(
                symbol, timeframe, limit=2, class_code=class_code
            )
            if bars:
                yield bars[-1]
            await asyncio.sleep(1.0)
