"""Research helpers: journal expectancy / contribution and walk-forward skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from engine.indicators import (
    equity_pnl,
    exposure_from_fills,
    notional_exposure_from_fills,
)

JOURNAL_STAT_EVENT_TYPES: tuple[str, ...] = (
    "execution.result",
    "ensemble.decision",
    "ensemble.reject",
    "risk.reject",
    "strategy.signal",
    "market.regime",
    "phantom.fills",
)


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    def to_dict(self) -> dict[str, int]:
        return {
            "fold": self.fold,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
        }


def expectancy_metrics(pnls: Sequence[float]) -> dict[str, Any]:
    """Classic trade expectancy + contribution-friendly aggregates."""
    values = [float(x) for x in pnls]
    n = len(values)
    if n == 0:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
        }
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    win_rate = len(wins) / n
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    expectancy = sum(values) / n
    if gross_loss > 1e-12:
        pf = gross_profit / gross_loss
    else:
        pf = 999.0 if gross_profit > 0 else 0.0
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "profit_factor": float(pf),
        "total_pnl": sum(values),
        "avg_pnl": expectancy,
    }


def _row_ts(row: dict[str, Any]) -> float:
    raw = row.get("timestamp")
    if raw is None and isinstance(row.get("payload"), dict):
        raw = row["payload"].get("ts") or row["payload"].get("timestamp")
    if raw is None:
        return 0.0
    if hasattr(raw, "timestamp"):
        try:
            return float(raw.timestamp())  # type: ignore[union-attr]
        except Exception:
            return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        # ISO-ish — keep stable lexicographic order via hash of string length proxy
        return float(len(str(raw)))


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return dict(p) if isinstance(p, dict) else {}


def _contributor_ids(payload: dict[str, Any], order: dict[str, Any] | None = None) -> list[str]:
    contributors = payload.get("contributors")
    out: list[str] = []
    if isinstance(contributors, list):
        for c in contributors:
            if isinstance(c, dict) and c.get("strategy_id"):
                out.append(str(c["strategy_id"]))
            elif isinstance(c, str) and c.strip():
                out.append(c.strip())
    if out:
        return out
    sid = ""
    if order:
        sid = str(order.get("strategy_id") or "")
    if not sid:
        sid = str(payload.get("strategy_id") or "")
    if sid and sid != "consilium":
        return [sid]
    return []


def estimate_fill_pnl(payload: dict[str, Any]) -> float | None:
    """Best-effort signed PnL for one execution / phantom fill."""
    for key in ("pnl", "realized_pnl", "attributed_pnl"):
        if payload.get(key) not in (None, ""):
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                pass
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    action = str(
        payload.get("action") or (order or {}).get("action") or ""
    ).upper()
    try:
        qty = float(payload.get("quantity") or (order or {}).get("quantity") or 0)
        price = float(payload.get("price") or (order or {}).get("price") or 0)
    except (TypeError, ValueError):
        return None
    if qty <= 0 or price <= 0:
        return None
    direction = (
        1.0
        if action in {"BUY", "LONG"}
        else -1.0
        if action in {"SELL", "SHORT", "CLOSE"}
        else 0.0
    )
    if direction == 0.0:
        return None
    # Same rough scale as ensemble_service attribution (not cash PnL).
    return direction * abs(qty) * price * 1e-4


def walk_forward_windows(
    n_bars: int,
    *,
    train_bars: int,
    test_bars: int,
    step: int | None = None,
) -> list[WalkForwardWindow]:
    """Anchored rolling windows: [train) then [test); advance by ``step`` (default=test)."""
    n = int(n_bars)
    train = max(1, int(train_bars))
    test = max(1, int(test_bars))
    stride = max(1, int(step if step is not None else test))
    out: list[WalkForwardWindow] = []
    i = 0
    fold = 0
    while True:
        train_start = i
        train_end = i + train
        test_start = train_end
        test_end = test_start + test
        if test_end > n:
            break
        out.append(
            WalkForwardWindow(
                fold=fold,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        i += stride
        fold += 1
    return out


def slice_series(
    series: dict[str, list[float]], start: int, end: int
) -> dict[str, list[float]]:
    return {k: list(v[start:end]) for k, v in series.items()}


def decisions_to_fills(
    decisions: Sequence[Any],
    closes: Sequence[float],
    *,
    quantity: float | None = 1.0,
    fee_bps: float = 0.0,
    score_from: int = 0,
    ticker: str = "RESEARCH",
    sizing: Any | None = None,
    start_equity: float = 10_000.0,
    atr: float | None = None,
    atr_series: Sequence[float | None] | None = None,
    carry_side: bool = True,
    position_mode: str = "long_flat",
) -> list[dict[str, Any]]:
    """Map bar decisions → fills with a signed target-position model.

    Contracts:
    - ``position_mode="long_flat"`` (default, matches paper ``forbid_net_flip``):
      ``BUY`` → long; ``SELL`` → flat (never opens a short). Always-in MACD
      sell regimes stay cash after exit — same as live side-change + no net flip.
    - ``position_mode="net_flip"``: ``SELL`` → short; long→short trades full delta.
    - ``HOLD`` + intent ``long|short`` → maintain (no rebalance).
    - ``HOLD`` + intent ``flat`` → flatten (true exits only; mean-reversion
      neutrals must emit maintain intent via ``neutral_hold``).
    - ``score_from`` / ``carry_side``: fee-free open at last train close when
      latent target is non-flat.
    - ``atr_series`` (preferred) is causal ATR per bar; scalar ``atr`` is fallback.
    """
    from engine.position_sizing import size_quantity

    mode = str(position_mode or "long_flat").strip().lower()
    if mode not in {"long_flat", "net_flip", "flatten"}:
        mode = "long_flat"
    if mode == "flatten":
        mode = "long_flat"

    def _atr_at(bar_index: int) -> float | None:
        if atr_series is not None and 0 <= bar_index < len(atr_series):
            val = atr_series[bar_index]
            return float(val) if val is not None else None
        return float(atr) if atr is not None else None

    def _qty_at(px: float, bar_index: int = 0) -> float:
        if sizing is not None:
            q = size_quantity(
                equity=float(start_equity),
                price=px,
                config=sizing,
                atr=_atr_at(bar_index),
            )
            return float(q) if q > 0 else 0.0
        return float(quantity) if quantity is not None else 1.0

    def _target_signed(decision: Any, px: float, bar_index: int) -> float | None:
        """Desired signed qty, or None = no change this bar."""
        if decision is None:
            return None
        action = str(getattr(decision, "action", None) or "").upper()
        intent = str(getattr(decision, "intent", None) or "").strip().lower()
        if action == "HOLD" and intent == "long":
            return None
        if action == "HOLD" and intent == "short":
            return None
        if action in {"BUY", "LONG"}:
            mag = _qty_at(px, bar_index)
            return mag if mag > 0 else None
        if action in {"SELL", "SHORT"}:
            if mode == "long_flat":
                return 0.0
            mag = _qty_at(px, bar_index)
            return -mag if mag > 0 else None
        if action == "HOLD" and intent in {"flat", "reduce"}:
            return 0.0
        if action == "HOLD":
            return None
        return None

    fills: list[dict[str, Any]] = []
    position = 0.0
    n = min(len(decisions), len(closes))
    sf = max(0, int(score_from))
    latent_target = 0.0

    def _sign(x: float) -> int:
        if x > 1e-12:
            return 1
        if x < -1e-12:
            return -1
        return 0

    def _apply_target(
        desired: float,
        px: float,
        bar_index: int,
        *,
        charge_fee: bool,
        carry: bool = False,
    ) -> None:
        nonlocal position
        if px <= 0:
            return
        delta = float(desired) - float(position)
        if abs(delta) <= 1e-12:
            return
        action = "BUY" if delta > 0 else "SELL"
        qty = abs(delta)
        cost = abs(qty * px) * float(fee_bps) / 10_000.0 if charge_fee else 0.0
        fills.append(
            {
                "action": action,
                "ticker": ticker,
                "quantity": qty,
                "price": px,
                "cost": cost,
                "bar_index": bar_index,
                "carry": bool(carry),
            }
        )
        position = float(desired)

    for i in range(min(sf, n)):
        t = _target_signed(decisions[i], float(closes[i]), i)
        if t is not None:
            latent_target = float(t)

    if carry_side and sf > 0 and abs(latent_target) > 1e-12:
        px = float(closes[sf - 1])
        mag = _qty_at(px, sf - 1)
        if mag > 0:
            desired = mag if latent_target > 0 else -mag
            if mode == "long_flat" and desired < 0:
                desired = 0.0
            if abs(desired) > 1e-12:
                _apply_target(desired, px, sf - 1, charge_fee=False, carry=True)

    for i in range(sf, n):
        px = float(closes[i])
        t = _target_signed(decisions[i], px, i)
        if t is None:
            continue
        new_sign = _sign(t)
        cur_sign = _sign(position)
        if new_sign == cur_sign and new_sign != 0:
            continue
        if new_sign == 0:
            desired = 0.0
        else:
            mag = _qty_at(px, i)
            if mag <= 0:
                continue
            desired = mag if new_sign > 0 else -mag
            if mode == "long_flat" and desired < 0:
                desired = 0.0
        _apply_target(desired, px, i, charge_fee=True)

    return fills


def buy_hold_oos_metrics(
    closes: Sequence[float],
    *,
    score_from: int = 0,
    quantity: float | None = 1.0,
    fee_bps: float = 0.0,
    start_equity: float = 10_000.0,
    sizing: Any | None = None,
    atr: float | None = None,
    atr_series: Sequence[float | None] | None = None,
) -> dict[str, Any]:
    """Long from first scored bar to last close (entry fee once; mark at end)."""
    from engine.indicators import equity_pnl
    from engine.position_sizing import size_quantity

    xs = [float(c) for c in closes]
    n = len(xs)
    sf = max(0, min(int(score_from), max(0, n - 1)))
    if n == 0 or sf >= n or xs[sf] <= 0:
        return equity_pnl([], start_equity=float(start_equity), mark_price=None)

    def _qty(px: float, i: int) -> float:
        if sizing is not None:
            a = atr
            if atr_series is not None and 0 <= i < len(atr_series):
                val = atr_series[i]
                a = float(val) if val is not None else None
            q = size_quantity(
                equity=float(start_equity), price=px, config=sizing, atr=a
            )
            return float(q) if q > 0 else 0.0
        return float(quantity) if quantity is not None else 1.0

    px = xs[sf]
    mag = _qty(px, sf)
    if mag <= 0:
        return equity_pnl([], start_equity=float(start_equity), mark_price=xs[-1])
    cost = abs(mag * px) * float(fee_bps) / 10_000.0
    fills = [
        {
            "action": "BUY",
            "ticker": "BUY_HOLD",
            "quantity": mag,
            "price": px,
            "cost": cost,
            "bar_index": sf,
            "carry": False,
        }
    ]
    metrics = equity_pnl(fills, start_equity=float(start_equity), mark_price=xs[-1])
    metrics["fills"] = len(fills)
    metrics["exposure"] = exposure_from_fills(fills, total_bars=len(xs))
    metrics["config"] = {"baseline": "buy_hold"}
    return metrics


def offline_variant_metrics(
    plugin: Any,
    series: dict[str, list[float]],
    *,
    config: dict[str, Any] | None = None,
    quantity: float | None = 1.0,
    fee_bps: float = 0.0,
    score_from: int = 0,
    start_equity: float = 10_000.0,
    sizing: Any | None = None,
    atr: float | None = None,
    atr_series: Sequence[float | None] | None = None,
    position_mode: str = "long_flat",
) -> dict[str, Any]:
    """Evaluate strategy on a series slice via SDK ``evaluate_bars`` → equity metrics."""
    if not hasattr(plugin, "evaluate_bars"):
        raise TypeError("plugin must implement evaluate_bars")
    from engine.position_sizing import SizingMode, atr_series_from_bars

    decisions = plugin.evaluate_bars(series, config=config)
    closes = list(series.get("close") or [])
    series_atr = atr_series
    if (
        series_atr is None
        and sizing is not None
        and getattr(sizing, "mode", None) == SizingMode.ATR_RISK
    ):
        series_atr = atr_series_from_bars(
            series.get("high") or [],
            series.get("low") or [],
            closes,
            period=int(getattr(sizing, "atr_period", 14) or 14),
        )
    fills = decisions_to_fills(
        decisions,
        closes,
        quantity=quantity,
        fee_bps=fee_bps,
        score_from=score_from,
        sizing=sizing,
        start_equity=start_equity,
        atr=atr,
        atr_series=series_atr,
        position_mode=position_mode,
    )
    mark_price = float(closes[-1]) if closes else None
    metrics = equity_pnl(
        fills, start_equity=float(start_equity), mark_price=mark_price
    )
    metrics["fills"] = len(fills)
    metrics["exposure"] = exposure_from_fills(fills, total_bars=len(closes))
    metrics["notional_exposure"] = notional_exposure_from_fills(
        fills, total_bars=len(closes)
    )
    metrics["config"] = dict(config or {})
    return metrics


def pick_best_variant(
    results: Sequence[dict[str, Any]], *, key: str = "pnl"
) -> dict[str, Any] | None:
    if not results:
        return None
    return max(results, key=lambda r: float(r.get(key) or 0.0))


def run_walk_forward_offline(
    plugin: Any,
    series: dict[str, list[float]],
    variants: Sequence[dict[str, Any]],
    *,
    train_bars: int = 60,
    test_bars: int = 20,
    step: int | None = None,
    quantity: float | None = 1.0,
    fee_bps: float = 0.0,
    start_equity: float = 10_000.0,
    config: Any = None,
    provenance_extra: dict[str, Any] | None = None,
    sizing: Any | None = None,
    atr: float | None = None,
    atr_series: Sequence[float | None] | None = None,
    position_mode: str = "long_flat",
) -> dict[str, Any]:
    """Delegate to ``engine.walk_forward`` (configurable selection + bootstrap)."""
    from engine.walk_forward import run_walk_forward_offline as _run

    return _run(
        plugin,
        series,
        variants,
        train_bars=train_bars,
        test_bars=test_bars,
        step=step,
        quantity=quantity,
        fee_bps=fee_bps,
        start_equity=start_equity,
        config=config,
        provenance_extra=provenance_extra,
        sizing=sizing,
        atr=atr,
        atr_series=atr_series,
        position_mode=position_mode,
    )


def aggregate_journal(
    rows: Sequence[dict[str, Any]],
    *,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    """
    Aggregate durable journal rows into expectancy / contribution stats.

    Attribution for fills: equal split across ``contributors`` when present,
    else ``order.strategy_id`` / ``strategy_id`` (skip bare ``consilium``).
    Regime tags use last seen ``market.regime`` while scanning oldest→newest.
    """
    ordered = sorted(rows, key=_row_ts)
    regime = "unknown"
    pnls_by_sid: dict[str, list[float]] = {}
    pnls_by_regime: dict[str, list[float]] = {}
    pnls_by_sid_regime: dict[str, list[float]] = {}
    counts = {
        "execution.result": 0,
        "ensemble.decision": 0,
        "ensemble.reject": 0,
        "risk.reject": 0,
        "strategy.signal": 0,
        "market.regime": 0,
        "phantom.fills": 0,
        "other": 0,
    }
    votes: dict[str, int] = {}
    rejects: dict[str, int] = {}
    observe_signals = 0

    for row in ordered:
        et = str(row.get("event_type") or "")
        if et in counts:
            counts[et] += 1
        else:
            counts["other"] += 1
        p = _payload(row)

        if et == "market.regime":
            regime = str(p.get("regime") or p.get("name") or regime or "unknown")
            continue

        if et == "strategy.signal":
            sid = str(p.get("strategy_id") or row.get("source_id") or "")
            if strategy_id and sid != strategy_id:
                continue
            if sid:
                votes[sid] = votes.get(sid, 0) + 1
            if p.get("observe_only"):
                observe_signals += 1
            continue

        if et in {"ensemble.reject", "risk.reject"}:
            sid = str(p.get("strategy_id") or "")
            if strategy_id and sid and sid != strategy_id:
                continue
            key = sid or str(p.get("reason") or "unknown")
            rejects[key] = rejects.get(key, 0) + 1
            continue

        if et == "ensemble.decision":
            for sid in _contributor_ids(p):
                if strategy_id and sid != strategy_id:
                    continue
                votes[sid] = votes.get(sid, 0) + 1
            continue

        if et not in {"execution.result", "phantom.fills"}:
            continue
        if et == "execution.result" and p.get("success") is False:
            continue

        order = p.get("order") if isinstance(p.get("order"), dict) else {}
        sids = _contributor_ids(p, order)
        if strategy_id:
            sids = [s for s in sids if s == strategy_id]
        if not sids:
            continue
        pnl = estimate_fill_pnl(p)
        if pnl is None:
            continue
        share = float(pnl) / len(sids)
        for sid in sids:
            pnls_by_sid.setdefault(sid, []).append(share)
            pnls_by_regime.setdefault(regime, []).append(share)
            pnls_by_sid_regime.setdefault(f"{sid}|{regime}", []).append(share)

    by_strategy = {sid: expectancy_metrics(v) for sid, v in sorted(pnls_by_sid.items())}
    by_regime = {r: expectancy_metrics(v) for r, v in sorted(pnls_by_regime.items())}
    by_strategy_regime = {
        k: expectancy_metrics(v) for k, v in sorted(pnls_by_sid_regime.items())
    }
    all_pnls = [x for xs in pnls_by_sid.values() for x in xs]
    contribution = {
        sid: float(stats.get("total_pnl") or 0.0) for sid, stats in by_strategy.items()
    }
    total_abs = sum(abs(v) for v in contribution.values()) or 1.0
    contribution_share = {sid: v / total_abs for sid, v in contribution.items()}

    return {
        "overall": expectancy_metrics(all_pnls),
        "by_strategy": by_strategy,
        "by_regime": by_regime,
        "by_strategy_regime": by_strategy_regime,
        "contribution": contribution,
        "contribution_share": contribution_share,
        "votes": votes,
        "rejects": rejects,
        "observe_signals": observe_signals,
        "counts": counts,
        "rows": len(rows),
        "last_regime": regime,
    }
