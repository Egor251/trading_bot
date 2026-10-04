"""Time-series momentum, as specified in the paper rather than as remembered.

Moskowitz, Ooi & Pedersen, "Time Series Momentum", Journal of Financial
Economics 2012: 58 liquid futures, 25 years, six asset classes, an effect they
call "remarkably consistent", later extended to 137 years by Hurst, Ooi &
Pedersen. Two details decide whether this reproduces anything:

  - the horizon is 1 to 12 months, strongest at monthly rebalancing and weakest
    at daily. This project measures at 41 days of hourly bars by default, which
    is the weakest corner of the documented effect. The daily run in the runbook
    is 2.7 years, and 1000 bars still only buys one full 12-month lookback plus
    ~600 bars to trade. That is the honest budget for this test.

  - position size is (40% / sigma_t), volatility-scaled. Without it the t-stats
    in the paper do not reproduce, and here it matters even more sharply: every
    other strategy in this repository trades a fixed quantity, so its result
    depends on the volatility of the period rather than on the signal. A
    fixed-size strategy measured against an unscaled random-timing null is not
    comparing two like quantities.

This plugin is deliberately faithful and deliberately narrow. It is not a
parameter to tune: `lookback`, `vol_window` and `target_vol` are the hypothesis,
and changing them until a P&L appears turns a test into the best-of-N illusion
this repository spent thirty commits removing.

Not in any lineup. If it proves anything it proves it through
tools/window_stability, against a null built from the same bars.
"""

from __future__ import annotations

import math
from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.strategy_plugin import StrategyDecision, StrategyPlugin


def _stdev(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var) if var > 0 else None


class TimeSeriesMomentumPlugin(StrategyPlugin):
    PLUGIN_ID = "time_series_momentum"
    PLUGIN_NAME = "Time Series Momentum (12m, vol-scaled)"
    RUN_MODE = PluginRunMode.CONTINUOUS

    def warmup_bars(self) -> int:
        return int(self.config.get("lookback", 252) or 252) + 2

    def evaluate_at(
        self,
        series: dict[str, Any],
        index: int,
        config: dict[str, Any] | None = None,
    ) -> StrategyDecision | None:
        cfg = config or self.isolated_config()
        lookback = max(2, int(cfg.get("lookback", 252) or 252))
        vol_window = max(5, int(cfg.get("vol_window", 30) or 30))
        target_vol = float(cfg.get("target_vol", 0.10) or 0.10)

        closes = [float(c) for c in (series.get("close") or [])[: index + 1]]
        need = lookback + vol_window
        if len(closes) < need:
            return StrategyDecision(action="HOLD", reason="warmup", intent="flat")

        price = closes[-1]
        past = closes[-(lookback + 1)]
        if past <= 0:
            return StrategyDecision(action="HOLD", reason="bad_price", intent="flat")
        ret = price / past - 1.0

        rets = [
            closes[i] / closes[i - 1] - 1.0
            for i in range(len(closes) - vol_window, len(closes))
            if closes[i - 1] > 0
        ]
        vol = _stdev(rets)
        if vol is None or vol <= 0:
            return StrategyDecision(action="HOLD", reason="no_vol", intent="flat")

        # The paper's (40% / sigma_t), expressed as a multiple of the base
        # quantity so this plugin needs no view of account equity. Scale by the
        # *target* rather than clamping at zero: a quiet market should be
        # traded larger, which is the whole point of the correction.
        scale = target_vol / (vol * math.sqrt(365.0))
        base_qty = float(cfg.get("quantity", 0.01) or 0.01)
        quantity = max(0.0, base_qty * scale)

        side = "BUY" if ret > 0 else "SELL"
        # Conviction is the normalised move, so strength_floor can express
        # "not worth trading" in units of volatility rather than percent.
        strength = min(1.0, abs(ret) / max(vol * math.sqrt(lookback), 1e-12))

        return StrategyDecision(
            action=side,
            strength=strength,
            reason=f"tsmom_{lookback}d",
            intent="long" if side == "BUY" else "short",
            metrics={
                "lookback": lookback,
                "ret": round(ret, 6),
                "vol_ann": round(vol * math.sqrt(365.0), 6),
                "scale": round(scale, 4),
                "quantity": round(quantity, 10),
                "close": price,
            },
        )

    async def on_initialize(self) -> None:
        await self.subscribe_event("market.candle", self._on_candle)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        if decision is None or decision.action == "HOLD":
            return
        payload = event.payload or {}
        close = float(payload.get("close") or 0)
        quantity = float(decision.metrics.get("quantity") or 0.0)
        if quantity <= 0:
            return
        await self.emit_signal(
            {
                **decision.as_signal_fields(),
                "class_code": payload.get(
                    "class_code", self.config.get("class_code", "CRYPTO")
                ),
                "ticker": payload.get("ticker"),
                "quantity": quantity,
                "order_type": "limit",
                "price": close,
            }
        )