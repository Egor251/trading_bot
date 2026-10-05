"""The YAML block must actually describe what the gate does.

A config key that the code reads but the file never mentions is invisible to the
operator, and a default that only exists in a dataclass is a default nobody chose.
These pin the shipped policy to the values that were reviewed, because the whole
point of this gate is that it is enforced and the whole point of enforcement is
that someone can see what is being enforced.
"""

from __future__ import annotations

from pathlib import Path

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import yaml  # noqa: E402

from engine.research_gate import LiveReadinessPolicy, policy_from_config  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "config" / "trading_bot.yaml"


def _live_readiness() -> dict:
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return raw["trading"]["live_readiness"]


def test_shipped_config_enables_the_multiplicity_correction():
    """The correction must be on in the shipped file, not merely available.

    An opt-in statistical safeguard is a footnote. An agent driving this system will
    never turn it on, and neither will anyone who has not read this test.
    """
    block = _live_readiness()
    assert block["require_multiplicity_correction"] is True
    assert block["multiplicity_method"] == "holm"
    assert block["multiplicity_alpha"] == 0.05


def test_shipped_config_still_enforces_the_gate():
    """Regression guard on the two knobs that must never silently relax."""
    block = _live_readiness()
    assert block["enforce"] is True
    assert block["require_ci_excludes_zero"] is True


def test_config_round_trips_into_the_policy():
    """The dataclass defaults and the YAML must agree.

    A mismatch means someone changed one and not the other, and the operator's file
    is the one they believe.
    """
    policy = policy_from_config(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))
    default = LiveReadinessPolicy()
    assert policy.require_multiplicity_correction == default.require_multiplicity_correction
    assert policy.multiplicity_method == default.multiplicity_method
    assert policy.multiplicity_alpha == default.multiplicity_alpha
    assert policy.enforce == default.enforce


def test_every_configured_policy_key_is_a_real_field():
    """An unrecognised key in `live_readiness` is silently dropped.

    `from_mapping` skips anything not in `fields(cls)`, which is the right
    behaviour for forward compatibility and the wrong behaviour for a typo: the
    file reads as if the knob were set, and nothing is.
    """
    policy = LiveReadinessPolicy.from_mapping(_live_readiness())
    known = set(policy.to_dict())
    for key in _live_readiness():
        assert key in known, f"{key!r} is in the YAML but not a policy field"