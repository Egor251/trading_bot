"""On-disk OHLC cache helpers (survive process restarts for WF batches)."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from engine.market import Candle


def cache_file_path(
    root: str | Path,
    *,
    provider: str,
    ticker: str,
    class_code: str,
    timeframe: str,
    limit: int,
    start: str | None,
    end: str | None,
) -> Path:
    raw = "|".join(
        [
            str(provider),
            str(ticker).upper(),
            str(class_code).upper(),
            str(timeframe),
            str(int(limit)),
            str(start or ""),
            str(end or ""),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    safe_t = "".join(ch if ch.isalnum() else "_" for ch in str(ticker).upper())[:24]
    return Path(root) / f"{safe_t}_{timeframe}_{digest}.json"


def candles_to_payload(candles: Sequence[Candle]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in candles:
        ts = c.ts
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            ts_s = ts.astimezone(UTC).isoformat()
        else:
            ts_s = str(ts)
        out.append(
            {
                "symbol": c.symbol,
                "timeframe": c.timeframe,
                "ts": ts_s,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume or 0.0),
                "exchange": c.exchange,
                "class_code": c.class_code,
            }
        )
    return out


def payload_to_candles(rows: Sequence[Mapping[str, Any]]) -> list[Candle]:
    out: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts_raw = row.get("ts")
        if isinstance(ts_raw, str) and ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(UTC)
        else:
            ts = datetime.now(UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        out.append(
            Candle(
                symbol=str(row.get("symbol") or ""),
                timeframe=str(row.get("timeframe") or ""),
                ts=ts,
                open=float(row.get("open") or 0.0),
                high=float(row.get("high") or 0.0),
                low=float(row.get("low") or 0.0),
                close=float(row.get("close") or 0.0),
                volume=float(row.get("volume") or 0.0),
                exchange=str(row.get("exchange") or ""),
                class_code=str(row.get("class_code") or ""),
            )
        )
    return out


def read_disk_cache(
    path: Path,
    *,
    ttl_sec: float,
    allow_stale: bool = False,
) -> list[Candle] | None:
    """Load cached candles. ``allow_stale`` returns expired files (API fallback)."""
    if not path.is_file():
        return None
    if ttl_sec <= 0 and not allow_stale:
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if ttl_sec > 0 and age > ttl_sec and not allow_stale:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    rows = raw.get("candles") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    return payload_to_candles(rows)


def write_disk_cache(
    path: Path,
    candles: Sequence[Candle],
    *,
    meta: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(UTC).isoformat(),
        "meta": dict(meta or {}),
        "candles": candles_to_payload(candles),
    }
    path.write_text(json.dumps(payload, indent=0, default=str), encoding="utf-8")
