"""Guards on operator-supplied risk limits and service config patches.

`SET_TRADING` is a single authenticated call that can hot-apply limits to the
live `RiskManager` and persist arbitrary `services.*` keys. Before these guards
it accepted `max_leverage: 500` and `ws_host: 0.0.0.0` without complaint.
"""

from __future__ import annotations

import pytest

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.risk_patch_guard import (  # noqa: E402
    RiskPatchRejected,
    ServicePatchRejected,
    guard_service_config_patch,
    validate_risk_patch,
)


def test_rejects_absurd_leverage_and_percentages() -> None:
    with pytest.raises(RiskPatchRejected, match="max_leverage"):
        validate_risk_patch({"max_leverage": 500}, {"max_leverage": 500.0})
    with pytest.raises(RiskPatchRejected, match="max_position_pct"):
        validate_risk_patch({"max_position_pct": 50}, {"max_position_pct": 50.0})
    with pytest.raises(RiskPatchRejected, match="max_daily_loss_pct"):
        validate_risk_patch({"max_daily_loss_pct": 2}, {"max_daily_loss_pct": 2.0})
    with pytest.raises(RiskPatchRejected, match="max_portfolio_drawdown"):
        validate_risk_patch(
            {"max_portfolio_drawdown": 5}, {"max_portfolio_drawdown": 5.0}
        )


def test_rejects_unknown_keys_instead_of_silently_ignoring() -> None:
    with pytest.raises(RiskPatchRejected, match="unknown risk limit"):
        validate_risk_patch({"max_leveragee": 2}, {})


def test_accepts_sane_limits_and_optional_disables() -> None:
    out = validate_risk_patch(
        {
            "max_leverage": 1.0,
            "max_position_pct": 0.2,
            "max_orders_per_minute": 10,
            "max_daily_loss_pct": 0.05,
        },
        {
            "max_leverage": 1.0,
            "max_position_pct": 0.2,
            "max_orders_per_minute": 10,
            "max_daily_loss_pct": 0.05,
        },
    )
    assert out["max_leverage"] == 1.0
    # `None` (disabled) is only accepted where the bridge normalises to it.
    assert validate_risk_patch({"max_data_age_seconds": 0}, {"max_data_age_seconds": None})


def test_service_patch_cannot_relocate_or_unauth_the_control_plane() -> None:
    # Binding the unauthenticated bridge off-host, then persisting it.
    with pytest.raises(ServicePatchRejected, match="ws_host"):
        guard_service_config_patch("ui_bridge_service", {"ws_host": "0.0.0.0"})
    with pytest.raises(ServicePatchRejected, match="ws_port"):
        guard_service_config_patch("ui_bridge_service", {"ws_port": 9999})
    # Disabling the authorizer from the same hook.
    with pytest.raises(ServicePatchRejected, match="allow_unauthenticated_local"):
        guard_service_config_patch(
            "live_safety_service", {"allow_unauthenticated_local": True}
        )
    with pytest.raises(ServicePatchRejected, match="hmac_secret_env"):
        guard_service_config_patch(
            "live_safety_service", {"hmac_secret_env": "MY_SECRET"}
        )


def test_service_patch_cannot_redirect_secret_env_or_patch_unknown_service() -> None:
    with pytest.raises(ServicePatchRejected, match="secret store"):
        guard_service_config_patch("ccxt_market_service", {"api_key_env": "LEAKED"})
    with pytest.raises(ServicePatchRejected, match="not patchable"):
        guard_service_config_patch("base_service", {"enabled": True})
    # The bridge itself is deliberately not in the allow-list.
    with pytest.raises(ServicePatchRejected, match="not patchable"):
        guard_service_config_patch("ui_bridge_service", {"hz": 4})


def test_service_patch_allows_legitimate_tuning() -> None:
    guard_service_config_patch("ensemble_service", {"debounce_sec": 0.2})
    guard_service_config_patch("ccxt_market_service", {"exchange": "binance"})
