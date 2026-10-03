"""The null hypothesis, run through the same machinery as the strategies.

Comparing a strategy's P&L against a coin flip tells you nothing, and I got that
wrong before writing this. The question that has an answer is: does this strategy
beat *random timing on the same bars*?

That comparison is only fair if the baseline pays exactly what the strategy pays
-- same commissions, same slippage, same sizing, same fill mechanics. Computing
a random baseline arithmetically and putting the two numbers side by side is the
classic mistake: the two P&Ls are not the same quantity.

So the null is a strategy. It ignores price entirely, draws from a seeded RNG,
and is evaluated by `run_grid` exactly like `supertrend_follow`. Comparability
is guaranteed by construction rather than by careful matching, which is the only
kind of guarantee that survives the next refactor.

Seeded, so a result can be reproduced exactly. An unseeded baseline cannot be
re-run, and a result nobody can reproduce is a result nobody should act on.

This is not shipped in any lineup and holds no position on a live venue. It is
research scaffolding: its job is to be unremarkable, and it should lose to a real
edge roughly as often as random timing wins.
"""

from __future__ import annotations

from typing import Any

from microkernel_core import KernelEvent, PluginRunMode

from engine.strategy_plugin import StrategyDecision, StrategyPlugin


class RandomBaselinePlugin(StrategyPlugin):
    """Enters and exits on a seeded random schedule, ignoring price."""

    PLUGIN_ID = "random_baseline"
    PLUGIN_NAME = "Random Timing Baseline"
    RUN_MODE = PluginRunMode.CONTINUOUS

    #: No warmup: this is not an indicator. Holding back the first bars would
    #: give the null a systematically different exposure profile from the
    #: strategies it is compared against.
    async def on_initialize(self) -> None:
        # Without this the plugin never sees a candle, never draws a schedule,
        # and the null measures nothing while looking like a result. It is the
        # reason the first null came back as all zeros across every window.
        await self.subscribe_event("market.candle", self._on_candle)

    async def _on_candle(self, event: KernelEvent) -> None:
        decision = await self.handle_candle(event)
        if decision is None or decision.action == "HOLD":
            return
        payload = event.payload or {}
        close = float(payload.get("close") or 0)
        await self.emit_signal(
            {
                **decision.as_signal_fields(),
                "class_code": payload.get(
                    "class_code", self.config.get("class_code", "CRYPTO")
                ),
                "ticker": payload.get("ticker"),
                "quantity": float(self.config.get("quantity", 0.01)),
                "order_type": "limit",
                "price": close,
            }
        )

    def warmup_bars(self) -> int:
        return 0

    def evaluate_at(
        self,
        series: dict[str, Any],
        index: int,
        config: dict[str, Any] | None = None,
    ) -> StrategyDecision | None:
        cfg = config or self.isolated_config()
        seed = int(cfg.get("seed", 0) or 0)
        prob = float(cfg.get("trade_probability", 0.08) or 0.08)
        if not 0.0 < prob <= 1.0:
            prob = 0.08

        # A fresh Random per call keyed on (seed, index): reproducible, and
        # independent of how many times the evaluator happens to be called. A
        # single Random carried on self would make the schedule depend on the
        # call sequence, which differs between the baseline and the strategy.
        import random as _random

        rng = _random.Random((seed << 20) ^ (index * 2654435761))
        if rng.random() >= prob:
            return StrategyDecision(action="HOLD", reason="no_draw", intent="flat")

        side = "BUY" if rng.random() < 0.5 else "SELL"
        return StrategyDecision(
            action=side,
            # Fixed strength, so this cannot accidentally out-size a strategy.
            strength=1.0,
            reason="random_timing",
            intent="long" if side == "BUY" else "short",
            metrics={"seed": seed, "index": index, "prob": prob},
        )