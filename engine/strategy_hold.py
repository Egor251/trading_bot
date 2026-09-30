"""Helpers for strategies that must hold through neutral / no-signal bars."""

from __future__ import annotations

from typing import Any

from engine.strategy_plugin import StrategyDecision, StrategyPlugin


def neutral_hold(
    plugin: StrategyPlugin,
    *,
    reason: str,
    metrics: dict[str, Any],
) -> StrategyDecision:
    """HOLD that *maintains* an open side (research + live parity).

    Plugins historically emitted ``HOLD`` + ``intent=flat`` for "neutral RSI /
    no impulse / inside channel". Live paths that ignore HOLD keep the book
    open; research ``decisions_to_fills`` treated flat as an exit — one-bar
    scalps and systematic fee bleed.

    Use this for no-signal bars: keep ``intent=long|short`` when ``_last_side``
    is set; only pure flat when already flat. Pair with ``emit_on_side_change``
    on the live candle path.
    """
    side = str(getattr(plugin, "_last_side", None) or "").upper()
    if side == "BUY":
        return StrategyDecision(
            action="HOLD", reason=reason, metrics=metrics, intent="long"
        )
    if side == "SELL":
        return StrategyDecision(
            action="HOLD", reason=reason, metrics=metrics, intent="short"
        )
    return StrategyDecision(
        action="HOLD", reason=reason, metrics=metrics, intent="flat"
    )


# Alias for pulse / breakout plugins (same contract as mean-reversion neutrals).
maintain_side_hold = neutral_hold
