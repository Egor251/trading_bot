"""Validation for operator-supplied risk limits and service config patches.

``SET_TRADING`` is a single authenticated call that can rewrite risk limits and
arbitrary ``services.*`` keys in ``config/trading_bot.yaml`` — which the engine
then hot-applies to the live ``RiskManager``.  Two concrete consequences of the
unvalidated version:

* ``{"risk": {"max_leverage": 500, "max_position_pct": 50,
  "max_daily_loss_pct": 0, "max_portfolio_drawdown": 0}}`` silently removed
  every ceiling.  ``RiskLimits`` is a bare dataclass with no ``__post_init__``,
  and the bridge treated ``<= 0`` as "disable this guard", so there was no
  floor anywhere in the path.
* ``{"services": {"ui_bridge_service": {"ws_host": "0.0.0.0"}}}`` persisted a
  LAN-reachable bind address for the unauthenticated control plane, and the
  same hook could rewrite ``hmac_secret_env`` / ``allow_unauthenticated_local``.

Every rejection here is an explicit, non-silent refusal with a reason the
operator UI can show.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "RiskPatchRejected",
    "ServicePatchRejected",
    "validate_risk_patch",
    "guard_service_config_patch",
    "SERVICE_ALLOWLIST",
    "FORBIDDEN_SERVICE_KEYS",
]


class RiskPatchRejected(ValueError):
    """A risk limit was outside its admissible range."""


class ServicePatchRejected(ValueError):
    """A service config key may not be written through the control plane."""


#: ``(min_exclusive, max_inclusive)`` for fractional limits.  ``None`` upper
#: bound means "no ceiling" — only legitimate for opt-in advanced caps.
_FRACTION_BOUNDS: dict[str, tuple[float, float | None]] = {
    "max_position_pct": (0.0, 1.0),
    "max_daily_loss_pct": (0.0, 1.0),
    "max_daily_turnover_pct": (0.0, None),
    "max_portfolio_drawdown": (0.0, 1.0),
    "max_strategy_drawdown": (0.0, 1.0),
    "max_concentration_pct": (0.0, 1.0),
    "max_order_adv_pct": (0.0, 1.0),
    "max_margin_utilization": (0.0, None),
    "kelly_fraction": (0.0, 1.0),
}

#: Absolute / count limits.  ``max_leverage`` is deliberately capped at 1.0: the
#: shipped configuration is unlevered, and raising it is a capital-structure
#: decision that must go through a config review, not a UI click.
_COUNT_BOUNDS: dict[str, tuple[float, float | None]] = {
    "max_leverage": (0.0, 1.0),
    "max_orders_per_minute": (1.0, 10_000.0),
    "max_data_age_seconds": (1.0, 86_400.0),
    "max_order_adv_bps": (0.0, 10_000.0),
}

_KNOWN_RISK_KEYS = frozenset(_FRACTION_BOUNDS) | frozenset(_COUNT_BOUNDS) | frozenset(
    {
        "max_correlation",
        "max_slippage_bps",
        "max_order_notional",
        "max_data_staleness_sec",
    }
)


def validate_risk_patch(
    requested: Mapping[str, Any], patched: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate a normalised risk patch, raising on anything unsafe.

    ``patched`` is the bridge's post-normalisation view (where ``<= 0`` on an
    optional cap has already become ``None``).  Returns the same mapping so the
    caller can use it as an expression.
    """
    for key, value in patched.items():
        if value is None:
            continue
        if key in _FRACTION_BOUNDS:
            low, high = _FRACTION_BOUNDS[key]
            number = float(value)
            if not (low < number <= (high if high is not None else float("inf"))):
                raise RiskPatchRejected(
                    f"{key}={number!r} is out of range; expected "
                    f"({low}, {high if high is not None else 'inf'}]"
                )
        elif key in _COUNT_BOUNDS:
            low, high = _COUNT_BOUNDS[key]
            number = float(value)
            if not (low <= number <= (high if high is not None else float("inf"))):
                raise RiskPatchRejected(
                    f"{key}={number!r} is out of range; expected "
                    f"[{low}, {high if high is not None else 'inf'}]"
                )

    # A limit that exists in the request but not in the normalised output is a
    # key the bridge does not know about — silently ignoring it would leave the
    # operator believing a ceiling was applied.
    unknown = set(requested) - _KNOWN_RISK_KEYS
    if unknown:
        raise RiskPatchRejected(
            f"unknown risk limit(s): {', '.join(sorted(unknown))}"
        )
    return dict(patched)


#: Services whose configuration may be patched from the Operator UI.
SERVICE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ccxt_market_service",
        "alpaca_market_service",
        "quik_market_service",
        "mock_market_service",
        "historical_data_service",
        "universe_service",
        "ensemble_service",
        "phantom_service",
        "execution_service",
        "storage_service",
        "trading_runtime_service",
        "ops_monitor_service",
    }
)

#: Keys that must never be writable through the control plane.  They either
#: relocate the unauthenticated control plane, or disable its authentication.
FORBIDDEN_SERVICE_KEYS: frozenset[str] = frozenset(
    {
        "ws_host",
        "ws_port",
        "pub_port",
        "sub_port",
        "hmac_secret_env",
        "evidence_secret_env",
        "live_feature_flag_env",
        "allow_unauthenticated_local",
        "kill_mode",
    }
)

#: Keys whose value names a secret-bearing environment variable.
_SECRET_ENV_KEY_SUFFIX = ("_env", "_secret_env", "_api_key_env", "_token_env")


def guard_service_config_patch(service_id: str, block: Mapping[str, Any]) -> None:
    """Refuse a service config patch that could widen the attack surface.

    Key-level checks run first so the operator sees *which* key was dangerous
    rather than a generic "service not patchable" message.
    """
    sid = str(service_id or "").strip()
    for key in block:
        name = str(key)
        if name in FORBIDDEN_SERVICE_KEYS:
            raise ServicePatchRejected(
                f"services.{sid}.{name} may not be set through the control plane"
            )
        if name.endswith(_SECRET_ENV_KEY_SUFFIX):
            raise ServicePatchRejected(
                f"services.{sid}.{name} may not be set through the control plane; "
                "use SET_SECRETS so the value goes to the secret store"
            )
    if sid not in SERVICE_ALLOWLIST:
        raise ServicePatchRejected(
            f"service {sid!r} is not patchable from the control plane "
            f"(allowed: {', '.join(sorted(SERVICE_ALLOWLIST))})"
        )
