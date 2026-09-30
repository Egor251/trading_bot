"""In-memory OHLCV ring buffer for active symbols."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Sequence


@dataclass(slots=True)
class StoredBar:
    symbol: str
    class_code: str
    ts: datetime | None
    open: float
    high: float
    low: float
    close: float
    volume: float
    key: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            self.key = f"{self.class_code}:{self.symbol}" if self.class_code else self.symbol


def _bar_unix(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return float(ts.timestamp())


def _bar_bucket(ts: datetime | None, date_s: str = "", time_s: str = "") -> str:
    """Minute bucket id for upsert (forming candle updates)."""
    if ts is not None:
        return ts.replace(second=0, microsecond=0).isoformat()
    if date_s and time_s:
        return f"{date_s}T{time_s[:5]}"
    return ""


class CandleStore:
    """Per-symbol ring buffer; strategies/risk read last bars / mark price."""

    def __init__(self, maxlen: int = 200) -> None:
        self.maxlen = max(1, maxlen)
        self._bars: dict[str, deque[StoredBar]] = defaultdict(
            lambda: deque(maxlen=self.maxlen)
        )
        self._rev: int = 0
        self._snap_cache: dict[tuple[Any, ...], dict[str, Any]] = {}

    def _bump(self) -> None:
        self._rev += 1
        self._snap_cache.clear()

    def _put(self, key: str, bar: StoredBar) -> None:
        dq = self._bars[key]
        if dq:
            prev = dq[-1]
            if _bar_bucket(prev.ts) and _bar_bucket(prev.ts) == _bar_bucket(bar.ts):
                if (
                    prev.open == bar.open
                    and prev.high == bar.high
                    and prev.low == bar.low
                    and prev.close == bar.close
                    and prev.volume == bar.volume
                ):
                    return
                dq[-1] = bar
                self._bump()
                return
        dq.append(bar)
        self._bump()

    def ingest_payload(self, payload: dict[str, Any]) -> StoredBar:
        symbol = str(payload.get("ticker") or payload.get("symbol") or "")
        class_code = str(payload.get("class_code") or "")
        key = f"{class_code}:{symbol}" if class_code else symbol
        ts = None
        date_s = str(payload.get("date") or "")
        time_s = str(payload.get("time") or "")
        if date_s and time_s:
            try:
                ts = datetime.fromisoformat(f"{date_s}T{time_s}")
            except ValueError:
                ts = None
        bar = StoredBar(
            symbol=symbol,
            class_code=class_code,
            ts=ts,
            open=float(payload.get("open") or 0),
            high=float(payload.get("high") or 0),
            low=float(payload.get("low") or 0),
            close=float(payload.get("close") or 0),
            volume=float(payload.get("volume") or 0),
            key=key,
        )
        self._put(key, bar)
        if class_code and symbol:
            self._put(symbol, bar)
        return bar

    def load_series(
        self,
        key: str,
        bars: Sequence[StoredBar],
        *,
        also_bare: bool = True,
    ) -> int:
        """Replace history for ``key`` (used for exchange seed on boot)."""
        seq = list(bars)[-self.maxlen :]
        self._bars[key] = deque(seq, maxlen=self.maxlen)
        if also_bare and ":" in key:
            bare = key.split(":", 1)[-1]
            self._bars[bare] = deque(seq, maxlen=self.maxlen)
        self._bump()
        return len(seq)

    def bars(self, symbol_or_key: str) -> list[StoredBar]:
        return list(self._bars.get(symbol_or_key, ()))

    def last(self, symbol_or_key: str) -> StoredBar | None:
        dq = self._bars.get(symbol_or_key)
        return dq[-1] if dq else None

    def mark_price(self, symbol_or_key: str, default: float = 0.0) -> float:
        bar = self.last(symbol_or_key)
        if bar is None or bar.close <= 0:
            return default
        return bar.close

    def symbols(self) -> list[str]:
        return sorted(k for k in self._bars if ":" in k)

    def closes(self, symbol_or_key: str, *, limit: int = 0) -> list[float]:
        bars = list(self._bars.get(symbol_or_key, ()))
        if limit > 0:
            bars = bars[-limit:]
        return [float(b.close) for b in bars]

    def ohlc(self, symbol_or_key: str, *, limit: int = 0) -> dict[str, list[float]]:
        bars = list(self._bars.get(symbol_or_key, ()))
        if limit > 0:
            bars = bars[-limit:]
        if not bars:
            return {
                "dates": [],
                "opens": [],
                "highs": [],
                "lows": [],
                "closes": [],
                "volumes": [],
            }
        dates: list[float] = []
        missing = 0
        for b in bars:
            u = _bar_unix(b.ts)
            if u is None:
                missing += 1
                dates.append(0.0)
            else:
                dates.append(u)
        if missing:
            # Fill gaps / all-missing with 1m steps ending at last known or now.
            end = next((d for d in reversed(dates) if d > 0), datetime.now(UTC).timestamp())
            for i in range(len(dates) - 1, -1, -1):
                if dates[i] <= 0:
                    dates[i] = float(end) - (len(dates) - 1 - i) * 60.0
                else:
                    end = dates[i]
        return {
            "dates": dates,
            "opens": [float(b.open or b.close) for b in bars],
            "highs": [float(b.high or b.close) for b in bars],
            "lows": [float(b.low or b.close) for b in bars],
            "closes": [float(b.close) for b in bars],
            "volumes": [float(b.volume or 0) for b in bars],
        }

    def snapshot(
        self,
        *,
        include_ohlc: bool = True,
        ohlc_limit: int = 0,
    ) -> dict[str, Any]:
        """UI/status payload.

        Omits the redundant top-level ``closes`` map (full series already live
        under ``ohlc[*].closes``). That duplicate used to blow past the TCP
        JSON-lines reader limit and disconnect the Operator bridge.

        ``ohlc_limit`` > 0 caps bars per symbol (keeps bridge/UI realtime).
        """
        lim = max(0, int(ohlc_limit or 0))
        cache_key = (self._rev, include_ohlc, lim)
        cached = self._snap_cache.get(cache_key)
        if cached is not None:
            return cached

        keys = [k for k in self._bars if ":" in k]
        last_closes: dict[str, float | None] = {}
        bar_counts: dict[str, int] = {}
        ohlc: dict[str, dict[str, list[float]]] = {}
        for k in keys:
            dq = self._bars[k]
            bar_counts[k] = len(dq)
            last_closes[k] = dq[-1].close if dq else None
            if include_ohlc:
                ohlc[k] = self.ohlc(k, limit=lim)
        out: dict[str, Any] = {
            "symbols": len(keys),
            "maxlen": self.maxlen,
            "rev": self._rev,
            "last_closes": last_closes,
            "bar_counts": bar_counts,
        }
        if include_ohlc:
            out["ohlc"] = ohlc
        self._snap_cache[cache_key] = out
        return out
