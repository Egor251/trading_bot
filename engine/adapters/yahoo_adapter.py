"""Yahoo Finance historical OHLCV via yfinance (sync wrapped in to_thread)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator

from engine.market import Candle


class YahooMarketDataAdapter:
    """
    Historical/fetch adapter for Yahoo symbols (e.g. SBER.ME, AAPL).

    Implements MarketDataPort; streaming is poll-of-latest only.
    """

    def __init__(self, *, exchange: str = "yahoo") -> None:
        self._exchange = exchange
        self._connected = False
        self._import_error: str | None = None

    @property
    def available(self) -> bool:
        try:
            import yfinance  # noqa: F401

            return True
        except ImportError as exc:
            self._import_error = str(exc)
            return False

    async def connect(self) -> None:
        if not self.available:
            raise ImportError(self._import_error or "yfinance not installed")
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        *,
        limit: int = 200,
        class_code: str = "",
        start: str | None = None,
        end: str | None = None,
    ) -> list[Candle]:
        if not self._connected:
            await self.connect()
        return await asyncio.to_thread(
            self._fetch_sync,
            symbol,
            timeframe,
            limit,
            class_code,
            start,
            end,
        )

    def _fetch_sync(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        class_code: str,
        start: str | None,
        end: str | None,
    ) -> list[Candle]:
        import yfinance as yf

        interval = _yahoo_interval(timeframe)
        if end is None:
            end_dt = datetime.now(UTC)
        else:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if start is None:
            # rough lookback from limit
            days = max(limit * _interval_days(interval, class_code), 7)
            start_dt = end_dt - timedelta(days=int(days))
        else:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))

        df = yf.download(
            symbol,
            start=start_dt.strftime("%Y-%m-%d"),
            end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval=interval,
            progress=False,
            auto_adjust=False,
        )
        if df is None or getattr(df, "empty", True):
            return []

        # yfinance may return MultiIndex columns
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = [c[0] for c in df.columns]

        out: list[Candle] = []
        for ts, row in df.iterrows():
            try:
                when = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                if getattr(when, "tzinfo", None) is None:
                    when = when.replace(tzinfo=UTC)
                else:
                    when = when.astimezone(UTC)
                out.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        ts=when,
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row.get("Volume") or 0),
                        exchange=self._exchange,
                        class_code=class_code,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if limit > 0:
            out = out[-int(limit) :]
        return out

    async def stream_candles(
        self,
        symbol: str,
        timeframe: str = "1d",
        *,
        class_code: str = "",
    ) -> AsyncIterator[Candle]:
        bars = await self.fetch_ohlcv(symbol, timeframe, limit=1, class_code=class_code)
        if bars:
            yield bars[-1]


def _yahoo_interval(timeframe: str) -> str:
    tf = (timeframe or "1d").lower()
    mapping = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "60m": "60m",
        "1d": "1d",
        "1w": "1wk",
        "1wk": "1wk",
    }
    return mapping.get(tf, "1d")


#: Calendar days per trading day: 365 / 252 sessions. Daily bars arrive on trading
#: days, so a lookback expressed in trading days must be widened by this much to
#: cover the same span in calendar time.
CALENDAR_DAYS_PER_TRADING_DAY = 365.0 / 252.0

#: Hours a Western equity session trades. Crypto trades continuously, so the two
#: asset classes need different intraday bar-to-calendar conversions and a single
#: assumption is wrong for one of them.
EQUITY_SESSION_HOURS = 6.5


def _interval_days(interval: str, class_code: str = "") -> float:
    """Calendar days one bar of this interval occupies.

    Every branch over-requests rather than under-requests, and deliberately so. A
    request that comes back short is a silent lie: the caller asked for N bars and
    measures whatever fraction arrived, and the shortfall surfaces later as a
    narrower sample rather than as an error. This one already did -- `1d` returned
    1.0, so asking for 300 daily bars fetched 300 *calendar* days and came back
    with about 206 bars of equity data, and every correlation computed on it was
    quietly computed on two thirds of the intended sample.

    `bars_per_trading_day` is the only thing that varies by asset class: crypto
    trades continuously, so it has 24 bars an hour, while a Western equity session
    is 6.25 hours of continuous trading inside a 6.5 hour session.
    """
    crypto = str(class_code).upper().startswith("CRYPTO")
    session_hours = 24.0 if crypto else EQUITY_SESSION_HOURS
    if interval.endswith("m") and interval[:-1].isdigit():
        bars_per_day = session_hours * 60.0 / float(interval[:-1])
    elif interval in {"1h", "60m"}:
        bars_per_day = session_hours
    elif interval in {"1wk", "1w"}:
        # One bar per five trading days.
        return CALENDAR_DAYS_PER_TRADING_DAY / 5.0
    else:
        # Daily and anything coarser: one trading day per bar.
        return CALENDAR_DAYS_PER_TRADING_DAY
    if bars_per_day <= 0:
        return CALENDAR_DAYS_PER_TRADING_DAY
    return CALENDAR_DAYS_PER_TRADING_DAY / bars_per_day
