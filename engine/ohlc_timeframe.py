"""OHLC timeframe helpers: parse, verify spacing, resample finer→coarser."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

from engine.market import Candle

_TF_SECONDS: dict[str, int] = {
    "1m": 60,
    "1min": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "60m": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86_400,
    "1day": 86_400,
    "d": 86_400,
    "1w": 604_800,
    "1week": 604_800,
}


class TimeframeMismatchError(RuntimeError):
    """Fetched bars do not match the requested timeframe spacing."""

    def __init__(
        self,
        *,
        requested: str,
        expected_sec: float,
        observed_sec: float,
        bars: int,
        provider: str = "",
    ) -> None:
        self.requested = requested
        self.expected_sec = float(expected_sec)
        self.observed_sec = float(observed_sec)
        self.bars = int(bars)
        self.provider = provider
        super().__init__(
            f"timeframe_mismatch: requested={requested} (~{expected_sec:.0f}s) "
            f"but median bar gap is ~{observed_sec:.0f}s "
            f"(bars={bars}, provider={provider or '-'})"
        )


def normalize_timeframe(tf: str | None, *, default: str = "1d") -> str:
    raw = str(tf or default).strip().lower()
    if not raw:
        return default
    aliases = {
        "min": "1m",
        "minute": "1m",
        "minutes": "1m",
        "hour": "1h",
        "hours": "1h",
        "day": "1d",
        "daily": "1d",
        "week": "1w",
        "weekly": "1w",
    }
    return aliases.get(raw, raw)


def timeframe_to_seconds(tf: str | None) -> int | None:
    key = normalize_timeframe(tf, default="")
    if not key:
        return None
    if key in _TF_SECONDS:
        return _TF_SECONDS[key]
    # e.g. "2d", "90m"
    if key.endswith("m") and key[:-1].isdigit():
        return int(key[:-1]) * 60
    if key.endswith("h") and key[:-1].isdigit():
        return int(key[:-1]) * 3600
    if key.endswith("d") and key[:-1].isdigit():
        return int(key[:-1]) * 86_400
    return None


def _ts_seconds(ts: Any) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return float(ts.timestamp())
    try:
        return float(ts)
    except (TypeError, ValueError):
        return None


def median_bar_interval_sec(candles: Sequence[Any]) -> float | None:
    """Median Δt between consecutive candle timestamps (seconds)."""
    stamps: list[float] = []
    for c in candles:
        ts = getattr(c, "ts", None)
        if ts is None and isinstance(c, dict):
            ts = c.get("ts") or c.get("timestamp") or c.get("time")
        sec = _ts_seconds(ts)
        if sec is not None:
            stamps.append(sec)
    if len(stamps) < 2:
        return None
    stamps.sort()
    gaps = [stamps[i] - stamps[i - 1] for i in range(1, len(stamps)) if stamps[i] > stamps[i - 1]]
    if not gaps:
        return None
    gaps.sort()
    return float(gaps[len(gaps) // 2])


def intervals_match(
    observed_sec: float,
    expected_sec: float,
    *,
    tol: float = 0.35,
) -> bool:
    if expected_sec <= 0 or observed_sec <= 0:
        return False
    return abs(observed_sec - expected_sec) / expected_sec <= tol


def _bucket_key(ts: datetime, timeframe: str) -> datetime:
    """Floor timestamp to the start of the timeframe bucket (UTC)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    else:
        ts = ts.astimezone(UTC)
    tf = normalize_timeframe(timeframe)
    if tf in {"1d", "1day", "d"} or (tf.endswith("d") and tf[:-1].isdigit()):
        days = 1 if tf in {"1d", "1day", "d"} else max(1, int(tf[:-1]))
        day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        if days == 1:
            return day
        # epoch-day buckets
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        n = int((day - epoch).days // days * days)
        return epoch + timedelta(days=n)
    if tf in {"1w", "1week"}:
        day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    sec = timeframe_to_seconds(tf) or 60
    epoch = int(ts.timestamp())
    floored = epoch - (epoch % sec)
    return datetime.fromtimestamp(floored, tz=UTC)


def resample_candles(
    candles: Sequence[Candle],
    timeframe: str,
) -> list[Candle]:
    """Aggregate finer bars into ``timeframe`` OHLC (causal, UTC buckets)."""
    tf = normalize_timeframe(timeframe)
    if len(candles) < 2:
        out = list(candles)
        for c in out:
            c.timeframe = tf
        return out

    observed = median_bar_interval_sec(candles)
    expected = timeframe_to_seconds(tf)
    if (
        observed is not None
        and expected is not None
        and intervals_match(observed, float(expected))
    ):
        out = list(candles)
        for c in out:
            c.timeframe = tf
        return out

    buckets: dict[datetime, list[Candle]] = defaultdict(list)
    for c in candles:
        ts = c.ts
        if ts is None:
            continue
        buckets[_bucket_key(ts, tf)].append(c)

    out: list[Candle] = []
    for key in sorted(buckets):
        rows = sorted(buckets[key], key=lambda x: _ts_seconds(x.ts) or 0.0)
        if not rows:
            continue
        out.append(
            Candle(
                symbol=rows[0].symbol,
                timeframe=tf,
                ts=key,
                open=float(rows[0].open),
                high=max(float(r.high) for r in rows),
                low=min(float(r.low) for r in rows),
                close=float(rows[-1].close),
                volume=sum(float(r.volume or 0) for r in rows),
                exchange=rows[0].exchange,
                class_code=rows[0].class_code,
            )
        )
    return out


def ensure_timeframe(
    candles: Sequence[Candle],
    timeframe: str,
    *,
    provider: str = "",
    allow_resample: bool = True,
) -> list[Candle]:
    """Return candles matching ``timeframe``, resampling when source is finer."""
    tf = normalize_timeframe(timeframe)
    rows = list(candles)
    if len(rows) < 2:
        for c in rows:
            c.timeframe = tf
        return rows

    expected = timeframe_to_seconds(tf)
    observed = median_bar_interval_sec(rows)
    if expected is None or observed is None:
        for c in rows:
            c.timeframe = tf
        return rows

    if intervals_match(observed, float(expected)):
        for c in rows:
            c.timeframe = tf
        return rows

    # Finer → coarser: resample.
    if allow_resample and observed < expected * (1.0 - 0.35):
        rows = resample_candles(rows, tf)
        observed2 = median_bar_interval_sec(rows)
        if observed2 is not None and intervals_match(observed2, float(expected)):
            return rows

    raise TimeframeMismatchError(
        requested=tf,
        expected_sec=float(expected),
        observed_sec=float(observed),
        bars=len(rows),
        provider=provider,
    )
