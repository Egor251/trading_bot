"""Synthetic MarketDataPort — realistic OHLC history for paper / UI."""

from __future__ import annotations

import random
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator

from engine.market import Candle


class MockMarketDataAdapter:
    """
    Per-symbol random-walk OHLC with wicks + volume.

    On first touch of a symbol, seeds ~120 bars so charts are not empty.
    Each ``fetch_ohlcv(..., limit=1)`` advances one new bar (stream tick).
    ``fetch_ohlcv(..., limit=N)`` with N>1 returns history (and may advance once).
    """

    def __init__(
        self,
        *,
        base_prices: dict[str, float] | None = None,
        exchange: str = "mock",
        seed_bars: int = 120,
        volatility: float = 0.004,
    ) -> None:
        self._base = dict(base_prices or {"SBER": 285.0, "GAZP": 165.0, "LKOH": 7200.0})
        self._exchange = exchange
        self._connected = False
        self._seed_bars = max(20, int(seed_bars))
        self._vol = max(0.0005, float(volatility))
        self._history: dict[str, deque[Candle]] = defaultdict(
            lambda: deque(maxlen=400)
        )
        self._px: dict[str, float] = {}
        self._rng: dict[str, random.Random] = {}
        self._step: dict[str, int] = defaultdict(int)

    async def connect(self) -> None:
        self._connected = True
        for sym in list(self._base):
            self.ensure_symbol(sym)

    async def disconnect(self) -> None:
        self._connected = False

    def ensure_symbol(self, ticker: str, price: float = 100.0) -> None:
        if ticker not in self._base:
            self._base[ticker] = price if price > 0 else 100.0
        if ticker not in self._rng:
            # Stable per-symbol RNG so restarts look coherent
            self._rng[ticker] = random.Random(hash(ticker) & 0xFFFFFFFF)
        if ticker not in self._px:
            self._px[ticker] = float(self._base[ticker])
        if len(self._history[ticker]) < self._seed_bars:
            self._seed(ticker)

    def _seed(self, symbol: str) -> None:
        hist = self._history[symbol]
        hist.clear()
        rng = self._rng[symbol]
        px = float(self._base[symbol])
        self._px[symbol] = px
        now = datetime.now(UTC)
        # Walk backward then reverse — start far below/near base with a trend + noise
        bars: list[Candle] = []
        t = now - timedelta(minutes=self._seed_bars)
        for _ in range(self._seed_bars):
            bar = self._make_bar(symbol, "1m", t, px, rng, class_code="")
            bars.append(bar)
            px = bar.close
            t += timedelta(minutes=1)
            # mild mean-reversion toward base
            px = px + (self._base[symbol] - px) * 0.02
        self._px[symbol] = px
        for b in bars:
            hist.append(b)
        self._step[symbol] = self._seed_bars

    def _make_bar(
        self,
        symbol: str,
        timeframe: str,
        ts: datetime,
        px: float,
        rng: random.Random,
        *,
        class_code: str,
    ) -> Candle:
        # Geometric-ish shock + occasional spike
        shock = rng.gauss(0.0, self._vol)
        if rng.random() < 0.04:
            shock += rng.choice((-1, 1)) * self._vol * rng.uniform(3.0, 7.0)
        # Intrabar path for realistic OHLC
        open_ = px
        close = max(0.01, px * (1.0 + shock))
        # 2–4 micro moves for high/low
        path = [open_]
        mid1 = open_ + (close - open_) * rng.uniform(0.2, 0.5) + px * rng.gauss(0, self._vol * 0.5)
        mid2 = open_ + (close - open_) * rng.uniform(0.5, 0.85) + px * rng.gauss(0, self._vol * 0.5)
        path.extend([mid1, mid2, close])
        high = max(path) * (1.0 + abs(rng.gauss(0, self._vol * 0.35)))
        low = min(path) * (1.0 - abs(rng.gauss(0, self._vol * 0.35)))
        low = max(0.01, min(low, open_, close))
        high = max(high, open_, close)
        body = abs(close - open_)
        vol = 800.0 + body / max(px, 1e-6) * 250_000 + rng.uniform(0, 1200)
        if abs(shock) > self._vol * 2:
            vol *= rng.uniform(1.8, 3.5)
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            ts=ts if ts.tzinfo else ts.replace(tzinfo=UTC),
            open=round(open_, 4),
            high=round(high, 4),
            low=round(low, 4),
            close=round(close, 4),
            volume=round(vol, 1),
            exchange=self._exchange,
            class_code=class_code,
            raw={"mock": True, "side": "buy" if close >= open_ else "sell"},
        )

    def _advance(self, symbol: str, timeframe: str, class_code: str) -> Candle:
        self.ensure_symbol(symbol)
        rng = self._rng[symbol]
        px = self._px[symbol]
        last = self._history[symbol][-1] if self._history[symbol] else None
        if last is not None:
            ts = last.ts + timedelta(minutes=1)
        else:
            ts = datetime.now(UTC)
        bar = self._make_bar(symbol, timeframe, ts, px, rng, class_code=class_code)
        # slight pull to base so price doesn't explode
        pulled = bar.close + (self._base[symbol] - bar.close) * 0.015
        if abs(pulled - bar.close) > 1e-9:
            # rebuild close-biased bar lightly
            bar = Candle(
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                ts=bar.ts,
                open=bar.open,
                high=max(bar.high, pulled),
                low=min(bar.low, pulled),
                close=round(pulled, 4),
                volume=bar.volume,
                exchange=bar.exchange,
                class_code=class_code or bar.class_code,
                raw=bar.raw,
            )
        self._px[symbol] = bar.close
        self._history[symbol].append(bar)
        self._step[symbol] += 1
        return bar

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        limit: int = 200,
        class_code: str = "",
    ) -> list[Candle]:
        self.ensure_symbol(symbol)
        n = max(1, int(limit))
        # Stream polls use limit=1 → always mint a fresh bar
        if n == 1:
            bar = self._advance(symbol, timeframe, class_code)
            if class_code and not bar.class_code:
                bar = Candle(
                    symbol=bar.symbol,
                    timeframe=bar.timeframe,
                    ts=bar.ts,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    exchange=bar.exchange,
                    class_code=class_code,
                    raw=bar.raw,
                )
            return [bar]
        # History fetch: return seed/walk without advancing (UI / backfill)
        hist = list(self._history[symbol])
        if class_code:
            hist = [
                Candle(
                    symbol=b.symbol,
                    timeframe=b.timeframe,
                    ts=b.ts,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    exchange=b.exchange,
                    class_code=class_code,
                    raw=b.raw,
                )
                for b in hist
            ]
        return hist[-n:]

    def history(self, symbol: str) -> list[Candle]:
        self.ensure_symbol(symbol)
        return list(self._history[symbol])

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
