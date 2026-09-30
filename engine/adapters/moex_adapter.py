"""MOEX ISS history via aiomoex (optional)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import AsyncIterator

from engine.market import Candle


class MoexMarketDataAdapter:
    """
    Board history for MOEX equities (default TQBR).

    ``symbol`` = secid (SBER); ``class_code`` = board (TQBR).
    """

    def __init__(self, *, exchange: str = "moex") -> None:
        self._exchange = exchange
        self._connected = False
        self._import_error: str | None = None

    @property
    def available(self) -> bool:
        try:
            import aiomoex  # noqa: F401
            import aiohttp  # noqa: F401

            return True
        except ImportError as exc:
            self._import_error = str(exc)
            return False

    async def connect(self) -> None:
        if not self.available:
            raise ImportError(self._import_error or "aiomoex/aiohttp not installed")
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        *,
        limit: int = 200,
        class_code: str = "TQBR",
        start: str | None = None,
        end: str | None = None,
    ) -> list[Candle]:
        if not self._connected:
            await self.connect()
        import aiomoex
        import aiohttp

        board = class_code or "TQBR"
        end_s = end or datetime.now(UTC).strftime("%Y-%m-%d")
        if start is None:
            days = max(int(limit) + 5, 30)
            start_s = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        else:
            start_s = start[:10]

        async with aiohttp.ClientSession() as session:
            rows = await aiomoex.get_board_history(
                session,
                symbol,
                start=start_s,
                end=end_s,
                board=board,
            )

        out: list[Candle] = []
        for row in rows or []:
            try:
                day = str(row.get("TRADEDATE") or row.get("tradedate") or "")
                if not day:
                    continue
                ts = datetime.fromisoformat(day).replace(tzinfo=UTC)
                close = float(row.get("CLOSE") or row.get("close") or 0)
                if close <= 0:
                    continue
                out.append(
                    Candle(
                        symbol=symbol,
                        timeframe="1d",
                        ts=ts,
                        open=float(row.get("OPEN") or row.get("open") or close),
                        high=float(row.get("HIGH") or row.get("high") or close),
                        low=float(row.get("LOW") or row.get("low") or close),
                        close=close,
                        volume=float(row.get("VOLUME") or row.get("volume") or 0),
                        exchange=self._exchange,
                        class_code=board,
                        raw=dict(row),
                    )
                )
            except (TypeError, ValueError):
                continue
        if limit > 0:
            out = out[-int(limit) :]
        return out

    async def stream_candles(
        self,
        symbol: str,
        timeframe: str = "1d",
        *,
        class_code: str = "TQBR",
    ) -> AsyncIterator[Candle]:
        bars = await self.fetch_ohlcv(symbol, timeframe, limit=1, class_code=class_code)
        if bars:
            yield bars[-1]
