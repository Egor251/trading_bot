"""Report what the research gate actually says about every shipped strategy.

The worry was that strategies are cleared on numbers produced by a pipeline that
has since changed. Rather than assume, read the gate's own verdict for every
member of the lineup and print the blockers it gives.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Egor\PycharmProjects\trading_bot")
sys.path.insert(0, str(ROOT))
from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from engine.research_gate import LiveReadinessPolicy, evaluate_many  # noqa: E402

LINEUP = [
    "adx_di_trend",
    "bollinger_reversion",
    "cci_reversion",
    "dual_thrust",
    "ema_cross",
    "heikin_ashi_trend",
    "keltner_breakout",
    "macd_trend",
    "ma_cross_example",
    "mean_reversion_example",
    "momentum_roc",
    "parabolic_sar_follow",
    "rsi_reversion",
    "stochastic_reversion",
    "supertrend_follow",
    "turtle_breakout",
    "volatility_breakout_example",
    "volume_impulse_example",
    "vwap_reversion",
    "williams_r_reversion",
]


def main() -> int:
    policy = LiveReadinessPolicy()
    out = evaluate_many(LINEUP, policy=policy)
    rows = out.get("rows") or []
    print(f"{'strategy':26} {'grade':14} {'live':>5}  failing checks (live)")
    print("-" * 96)
    counts: dict[str, int] = {}
    for row in rows:
        sid = str(row.get("strategy_id") or "?")
        grade = str(row.get("grade") or "?")
        counts[grade] = counts.get(grade, 0) + 1
        failing = [
            str(c.get("name"))
            for c in (row.get("checks") or [])
            if not c.get("ok") and c.get("live")
        ]
        print(
            f"{sid:26} {grade:14} {str(row.get('live_ok')):>5}  "
            f"{', '.join(failing) if failing else '-'}"
        )
    print()
    print("grades:", counts)
    print("live_ok:", out.get("live_ok"))
    print("observe_ok:", out.get("observe_ok"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())