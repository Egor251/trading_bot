"""Pair journal fills into round-trip trades for operator audit tools."""

from __future__ import annotations

from typing import Any


def _side_sign(side: str) -> int:
    s = str(side or "").strip().lower()
    if s in {"buy", "long", "b"}:
        return 1
    if s in {"sell", "short", "s", "close"}:
        return -1
    return 0


def normalize_fill_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten a journal / fill row into a common fill dict."""
    if not isinstance(row, dict):
        return None
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
    sid = str(
        payload.get("strategy_id")
        or order.get("strategy_id")
        or raw.get("strategy_id")
        or row.get("strategy_id")
        or ""
    ).strip()
    ticker = str(
        payload.get("ticker")
        or payload.get("symbol")
        or order.get("symbol")
        or row.get("symbol")
        or ""
    ).strip()
    class_code = str(
        payload.get("class_code") or order.get("class_code") or row.get("class_code") or ""
    ).strip()
    symbol = f"{class_code}:{ticker}" if class_code and ticker else ticker
    if not symbol:
        return None
    side = str(
        order.get("side")
        or payload.get("action")
        or payload.get("side")
        or row.get("side")
        or ""
    )
    try:
        qty = float(
            order.get("filled_qty")
            or payload.get("quantity")
            or order.get("quantity")
            or row.get("quantity")
            or 0
        )
        price = float(
            order.get("avg_price")
            or payload.get("price")
            or order.get("price")
            or row.get("price")
            or 0
        )
    except (TypeError, ValueError):
        return None
    if qty <= 0 or price <= 0:
        return None
    sign = _side_sign(side)
    if sign == 0:
        return None
    pnl = None
    for key in ("pnl", "realized_pnl", "attributed_pnl"):
        src = payload.get(key) if payload.get(key) not in (None, "") else row.get(key)
        if src not in (None, ""):
            try:
                pnl = float(src)
            except (TypeError, ValueError):
                pnl = None
            break
    costs = None
    try:
        if payload.get("costs") not in (None, ""):
            costs = float(payload.get("costs"))
        elif row.get("total_cost") not in (None, ""):
            costs = float(row.get("total_cost"))
    except (TypeError, ValueError):
        costs = None
    return {
        "ts": row.get("ts") or row.get("created_at") or payload.get("ts"),
        "event_type": row.get("event_type") or payload.get("event_type") or "fill",
        "strategy_id": sid,
        "symbol": symbol,
        "side": "buy" if sign > 0 else "sell",
        "quantity": qty,
        "price": price,
        "status": str(order.get("status") or payload.get("status") or row.get("status") or ""),
        "reason": str(order.get("reason") or payload.get("reason") or ""),
        "client_id": order.get("client_id") or payload.get("client_id"),
        "pnl": pnl,
        "costs": costs,
        "cash_delta": row.get("cash_delta") or payload.get("cash_delta"),
    }


def pair_round_trips(
    fills: list[dict[str, Any]],
    *,
    strategy_id: str | None = None,
    symbol: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """FIFO-pair fills into closed trades; return open legs separately."""
    sid_f = str(strategy_id or "").strip() or None
    sym_f = str(symbol or "").strip() or None
    open_lots: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []

    for fill in fills:
        if not isinstance(fill, dict):
            continue
        sid = str(fill.get("strategy_id") or "").strip()
        sym = str(fill.get("symbol") or "").strip()
        if sid_f and sid != sid_f:
            continue
        if sym_f and sym_f not in {sym, sym.split(":")[-1]} and sym_f not in sym:
            continue
        sign = _side_sign(str(fill.get("side") or ""))
        if sign == 0:
            continue
        try:
            qty = float(fill.get("quantity") or 0)
            price = float(fill.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        ts = fill.get("ts")
        remaining = qty
        i = 0
        while remaining > 1e-12 and i < len(open_lots):
            lot = open_lots[i]
            if lot["strategy_id"] != sid or lot["symbol"] != sym:
                i += 1
                continue
            if int(lot["sign"]) == sign:
                i += 1
                continue
            close_qty = min(float(lot["qty_left"]), remaining)
            entry_px = float(lot["entry_price"])
            realized = float(lot["sign"]) * close_qty * (price - entry_px)
            closed.append(
                {
                    "strategy_id": sid,
                    "symbol": sym,
                    "entry_ts": lot.get("entry_ts"),
                    "exit_ts": ts,
                    "entry_price": entry_px,
                    "exit_price": price,
                    "quantity": close_qty,
                    "side": "long" if lot["sign"] > 0 else "short",
                    "realized_pnl": round(realized, 8),
                }
            )
            lot["qty_left"] = float(lot["qty_left"]) - close_qty
            remaining -= close_qty
            if float(lot["qty_left"]) <= 1e-12:
                open_lots.pop(i)
            else:
                i += 1
        if remaining > 1e-12:
            open_lots.append(
                {
                    "strategy_id": sid,
                    "symbol": sym,
                    "sign": sign,
                    "qty_left": remaining,
                    "entry_price": price,
                    "entry_ts": ts,
                }
            )

    closed = closed[-limit:]
    open_legs = [
        {
            "strategy_id": lot["strategy_id"],
            "symbol": lot["symbol"],
            "side": "long" if lot["sign"] > 0 else "short",
            "quantity": lot["qty_left"],
            "entry_price": lot["entry_price"],
            "entry_ts": lot.get("entry_ts"),
        }
        for lot in open_lots
    ]
    return {
        "trades": list(reversed(closed)),
        "open_legs": open_legs,
        "closed_count": len(closed),
        "open_count": len(open_legs),
    }


def summarize_cost_schedule(
    schedule: dict[str, Any] | None, *, source: str | None = None
) -> str:
    """Short human-readable blurb for CostSchedule.to_dict()."""
    if not isinstance(schedule, dict):
        return "no active cost schedule"
    version = schedule.get("version") or "?"
    rules = schedule.get("rules") or schedule.get("entries") or []
    lines = [
        f"version={version} source={source or '?'} "
        f"rules={len(rules) if isinstance(rules, list) else 0}"
    ]
    if isinstance(rules, list):
        for rule in rules[:12]:
            if not isinstance(rule, dict):
                continue
            match = rule.get("match") or rule.get("selector") or {}
            if isinstance(match, dict):
                match_s = ",".join(
                    f"{k}={v}" for k, v in match.items() if v not in (None, "")
                )
            else:
                match_s = str(match)
            comps = rule.get("components") or rule.get("costs") or []
            bits: list[str] = []
            if isinstance(comps, list):
                for c in comps[:6]:
                    if isinstance(c, dict):
                        bits.append(
                            f"{c.get('kind') or c.get('name') or '?'}={c.get('rate')}"
                        )
            lines.append(f"- {match_s or '*'}: {', '.join(bits) if bits else 'n/a'}")
        if len(rules) > 12:
            lines.append(f"… +{len(rules) - 12} more rules")
    return "\n".join(lines)
