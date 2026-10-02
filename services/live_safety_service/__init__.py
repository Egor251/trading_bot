"""Control authentication and fail-closed live preflight ownership."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from microkernel_core import BaseService

from engine.control_auth import (
    AuthorizationDenied,
    AuthSession,
    CommandAuthorization,
    ControlAuthorizer,
    ControlCommand,
    HMACTokenAuth,
    Role,
)
from engine.live_safety import EvidenceAuthority, KillMode, LivePreflight

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore  # noqa: F401 - typing only


def _log() -> Any:
    try:
        from utils.prod_logging import get_logger

        return get_logger("live_safety")
    except Exception:  # noqa: BLE001 - logging must never block boot
        import logging

        return logging.getLogger("live_safety")


class UnavailableControlAuthorizer:
    """HALT remains available; every privilege-increasing command fails closed."""

    mode = "unavailable"

    def authorize(
        self,
        command: ControlCommand | str,
        *,
        token: str | None = None,
        nonce: str | None = None,
    ) -> CommandAuthorization:
        command = ControlCommand(command)
        if command is ControlCommand.HALT:
            return CommandAuthorization(command, True, True, reason="emergency_halt")
        raise AuthorizationDenied("control authentication is not configured")


class LocalDevControlAuthorizer:
    """Paper / same-machine Operator UI when no HMAC secret is installed.

    Grants ``CONFIG`` so the desktop/web Operator stays usable on a laptop, and
    refuses everything that can increase risk:

    * ``RESUME`` — an unauthenticated peer must not be able to re-arm a bot
      that risk management halted;
    * ``LIVE``   — real money requires a signed token.

    This mode is opt-in (``allow_unauthenticated_local``) and logs a loud
    warning at boot, because a laptop default of "everything is authorised"
    is how an unauthenticated control plane ships to production.
    """

    mode = "local_dev"

    def authorize(
        self,
        command: ControlCommand | str,
        *,
        token: str | None = None,
        nonce: str | None = None,
    ) -> CommandAuthorization:
        command = ControlCommand(command)
        if command is ControlCommand.HALT:
            return CommandAuthorization(command, True, True, reason="emergency_halt")
        if command is ControlCommand.LIVE:
            raise AuthorizationDenied(
                "live mode requires TRADING_CONTROL_HMAC_SECRET "
                "(≥32 bytes) and a signed operator token"
            )
        if command is ControlCommand.RESUME:
            raise AuthorizationDenied(
                "resuming trading requires TRADING_CONTROL_HMAC_SECRET "
                "(≥32 bytes) and a signed operator token"
            )
        principal = AuthSession(
            subject="local_operator",
            roles=frozenset({Role.ADMIN, Role.OPERATOR}),
            issued_at=0.0,
            expires_at=0.0,
            session_id="local_dev",
        )
        return CommandAuthorization(
            command,
            True,
            False,
            principal,
            reason="local_dev_unauthenticated",
        )


class LiveSafetyService(BaseService):
    def default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "auto_start": True,
            "hmac_secret_env": "TRADING_CONTROL_HMAC_SECRET",
            "evidence_secret_env": "TRADING_EVIDENCE_HMAC_SECRET",
            "live_feature_flag_env": "TRADING_LIVE_ENABLED",
            "kill_mode": "halt_only",
            # Fail-closed default: with no HMAC secret installed, only HALT is
            # accepted.  Set true explicitly to allow an unsigned local Operator
            # UI (paper / laptop), which grants CONFIG but never RESUME or LIVE.
            "allow_unauthenticated_local": False,
        }

    async def on_initialize(self) -> None:
        auth_secret = os.environ.get(
            str(self.config.get("hmac_secret_env") or "TRADING_CONTROL_HMAC_SECRET"),
            "",
        )
        evidence_secret = os.environ.get(
            str(
                self.config.get("evidence_secret_env")
                or "TRADING_EVIDENCE_HMAC_SECRET"
            ),
            "",
        )
        allow_local = bool(self.config.get("allow_unauthenticated_local", False))
        if len(auth_secret.encode()) >= 32:
            authorizer: Any = ControlAuthorizer(HMACTokenAuth(auth_secret))
        elif allow_local:
            authorizer = LocalDevControlAuthorizer()
            _log().critical(
                "CONTROL AUTH DEGRADED: TRADING_CONTROL_HMAC_SECRET is not set, "
                "so CONFIG commands are accepted unauthenticated. RESUME and "
                "LIVE remain refused. Set TRADING_CONTROL_HMAC_SECRET (>=32 bytes) "
                "before exposing this host beyond the loopback interface."
            )
        else:
            authorizer = UnavailableControlAuthorizer()
            _log().critical(
                "CONTROL AUTH UNAVAILABLE: no TRADING_CONTROL_HMAC_SECRET and "
                "allow_unauthenticated_local=false. Only HALT is accepted; every "
                "other control command will be denied until a secret is installed."
            )
        if len(evidence_secret.encode()) >= 32:
            evidence_authority = EvidenceAuthority(evidence_secret)
            preflight = LivePreflight(
                evidence_authority,
                feature_flag_env=str(
                    self.config.get("live_feature_flag_env")
                    or "TRADING_LIVE_ENABLED"
                ),
                kill_mode=KillMode(
                    str(self.config.get("kill_mode") or "halt_only")
                ),
            )
        else:
            evidence_authority = None
            preflight = None
        setattr(self.core, "control_authorizer", authorizer)
        setattr(self.core, "evidence_authority", evidence_authority)
        setattr(self.core, "live_preflight", preflight)

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        authorizer = getattr(self.core, "control_authorizer", None)
        if isinstance(authorizer, ControlAuthorizer):
            auth_mode = "hmac"
        else:
            auth_mode = str(getattr(authorizer, "mode", "none") or "none")
        status.update(
            {
                "control_auth_configured": isinstance(authorizer, ControlAuthorizer),
                "control_auth_mode": auth_mode,
                "evidence_auth_configured": getattr(
                    self.core, "evidence_authority", None
                )
                is not None,
                "live_feature_flag": bool(
                    os.environ.get(
                        str(
                            self.config.get("live_feature_flag_env")
                            or "TRADING_LIVE_ENABLED"
                        )
                    )
                ),
            }
        )
        return status
