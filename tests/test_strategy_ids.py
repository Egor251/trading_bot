"""Strategy id alias / resolve tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.strategy_ids import resolve_strategy_id  # noqa: E402


def test_aliases_and_example_suffix() -> None:
    loaded = {
        "ma_cross_example",
        "volume_impulse_example",
        "bollinger_reversion",
        "mean_reversion_example",
    }

    def ok(sid: str) -> bool:
        return sid in loaded

    assert resolve_strategy_id("ma_cross", is_loaded=ok, loaded_ids=list(loaded)) == (
        "ma_cross_example"
    )
    assert resolve_strategy_id(
        "volume_impulse", is_loaded=ok, loaded_ids=list(loaded)
    ) == "volume_impulse_example"
    assert resolve_strategy_id(
        "mean_reversion", is_loaded=ok, loaded_ids=list(loaded)
    ) == "mean_reversion_example"
    assert resolve_strategy_id(
        "bollinger_reversion", is_loaded=ok, loaded_ids=list(loaded)
    ) == "bollinger_reversion"
    assert resolve_strategy_id(
        "bollinger", is_loaded=ok, loaded_ids=list(loaded)
    ) == "bollinger_reversion"


if __name__ == "__main__":
    test_aliases_and_example_suffix()
    print("strategy_ids OK")
