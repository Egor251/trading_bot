"""The research fill mode must read the key the shipped config actually uses."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from services.phantom_service import research_position_mode  # noqa: E402


def test_shipped_position_policy_is_honoured():
    assert research_position_mode(
        {"position_policy": {"forbid_net_flip": False}}
    ) == "net_flip"
    assert research_position_mode(
        {"position_policy": {"forbid_net_flip": True}}
    ) == "long_flat"


def test_missing_or_legacy_key_fails_closed_to_long_flat():
    assert research_position_mode({}) == "long_flat"
    assert research_position_mode({"forbid_net_flip": False}) == "long_flat"
    assert research_position_mode(None) == "long_flat"


def test_service_config_is_the_fallback():
    assert research_position_mode({}, {"forbid_net_flip": False}) == "net_flip"
