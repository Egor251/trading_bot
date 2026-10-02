"""Single source of truth for control-plane command classification.

Both the engine-side authorizer (:mod:`services.ui_bridge_service`) and the
UI-side token attacher (:mod:`ui.control_auth_client`) must agree on *which*
commands are privileged.  Keeping two hand-maintained sets in sync by hand is
how ``CANCEL_ALL_ORDERS`` silently stopped working when HMAC auth was enabled.

The classification is intentionally boring and side-effect free so it can be
imported from the engine host, the UI process and tests without pulling in
FastAPI or a running kernel.
"""

from __future__ import annotations

from typing import Any, Mapping

from engine.control_auth import ControlCommand

__all__ = [
    "CONTROL_COMMANDS",
    "READ_ONLY_COMMANDS",
    "classify_control_command",
    "requires_authorization",
    "HALT_COMMANDS",
    "RESUME_COMMANDS",
    "LIVE_BOOK_COMMANDS",
    "CONFIG_COMMANDS",
    "AI_COMMANDS",
]

# --- command groups ---------------------------------------------------------

#: Always available, even without credentials — loss of auth must never block
#: risk reduction.
HALT_COMMANDS = frozenset({"KILL", "HALT", "STOP", "EMERGENCY"})

#: Clears ``risk.halted`` and restarts trading.  Deliberately *not* part of the
#: local-dev allow-list: an unauthenticated peer must not be able to re-arm a
#: bot that risk management halted.
RESUME_COMMANDS = frozenset({"RESUME", "START"})

#: Book mutations.  These need ``LIVE`` authority in live/quik mode and
#: ``CONFIG`` in paper mode.
LIVE_BOOK_COMMANDS = frozenset({"PLACE_ORDER", "CLOSE_POSITION", "CLOSE_ALL"})

#: Configuration / state mutations.  ``ADMIN`` only.
CONFIG_COMMANDS = frozenset(
    {
        "SAVE_CONFIG",
        "GET_CONFIG_RAW",
        "SET_UNIVERSE",
        "SET_TRADING",
        "SET_ENSEMBLE",
        "SET_PLUGIN",
        "SET_STRATEGY_PARAMS",
        # Risk limits and the strength floor. These decide what an order is
        # allowed to be, so they belong with the other ADMIN mutations rather
        # than being reachable by a lesser role.
        "SET_RISK",
        "SET_EXECUTION",
        # Decides when a strategy is stopped from trading on its own results.
        "SET_QUARANTINE",
        "SET_AI",
        "SET_SECRETS",
        "SET_STORAGE",
        "COST_SAVE_DRAFT",
        "COST_ACTIVATE",
        "RESET_PAPER",
        "CLEAR_PAPER",
        "RESET_SIM",
        "PHANTOM_RESET",
        "PHANTOM_CAPTURE",
        "PHANTOM_RUN",
        "PHANTOM_WALK_FORWARD",
        "PHANTOM_WALK_FORWARD_BATCH",
        "PHANTOM_APPLY_CHOSEN",
        "PHANTOM_SYNC_METRICS",
        "CANCEL_ORDER",
        "CANCEL_ALL_ORDERS",
        "RELEASE_EXIT",
        "RECONCILE_NOW",
    }
)

#: AI assistant surface.  Authorized so that the *request* carries a verified
#: principal, and so ``caller_role`` can be derived from that principal instead
#: of trusting a caller-supplied field.  The tools reachable from here run with
#: a reduced role (see :mod:`engine.ai_tools`) and therefore cannot reach
#: ``CONFIG`` / ``RESUME`` / ``LIVE``.
AI_COMMANDS = frozenset({"ASK"})

#: Pure reads — no authorization, no rate limiting.
READ_ONLY_COMMANDS = frozenset({"PING", "STATUS", "GET_CONFIG"})

#: Every command that must carry a token when the control plane is configured.
CONTROL_COMMANDS = frozenset(
    HALT_COMMANDS
    | RESUME_COMMANDS
    | LIVE_BOOK_COMMANDS
    | CONFIG_COMMANDS
    | AI_COMMANDS
)

#: Every command the bridge knows how to dispatch.  Used as a tripwire: a new
#: command that is neither read-only nor classified is treated as privileged
#: (fail-closed) rather than silently open.
_ALL_KNOWN = CONTROL_COMMANDS | READ_ONLY_COMMANDS


def requires_authorization(raw: str) -> bool:
    """True when *raw* must carry an auth token.

    Unknown commands are treated as privileged.  Adding a new command to the
    bridge without registering it here denies it under HMAC auth, which is the
    safe direction to fail.
    """
    name = str(raw or "").strip().upper()
    if not name:
        return False
    if name in READ_ONLY_COMMANDS:
        return False
    return True


def classify_control_command(
    raw: str,
    *,
    live_mode: bool = False,
    requested_mode: str | None = None,
) -> ControlCommand | None:
    """Map a raw command name onto the authorisation class it needs.

    ``live_mode`` reflects the *current* runtime mode and decides whether book
    mutations are treated as live operations.  ``requested_mode`` covers
    commands that switch the mode themselves (``SET_TRADING mode=live``), which
    must be authorised as ``LIVE`` even while the bot is still in paper.

    Returns ``None`` for read-only commands.  Unknown commands resolve to
    ``ControlCommand.CONFIG`` so they fail closed.
    """
    name = str(raw or "").strip().upper()
    if not name or name in READ_ONLY_COMMANDS:
        return None
    if name in HALT_COMMANDS:
        return ControlCommand.HALT
    if name in RESUME_COMMANDS:
        return ControlCommand.RESUME
    if name in LIVE_BOOK_COMMANDS:
        return ControlCommand.LIVE if live_mode else ControlCommand.CONFIG
    if name in AI_COMMANDS:
        # The AI drawer is a read/analysis surface plus explicitly confirmed
        # risk-reducing tools.  CONFIG is the right class: the individual tools
        # are further constrained by the role attached to the token.
        return ControlCommand.CONFIG
    if name == "SET_TRADING":
        if str(requested_mode or "").strip().lower() in {"live", "quik"}:
            return ControlCommand.LIVE
        return ControlCommand.CONFIG
    if name in _ALL_KNOWN:
        return ControlCommand.CONFIG
    # Unknown → privileged default.
    return ControlCommand.CONFIG


def is_live_mode(runtime: Any) -> bool:
    """True when the runtime trades real money (or is wired to a live broker)."""
    mode = str(getattr(runtime, "mode", "") or "").strip().lower()
    return mode in {"live", "quik"}


def command_needs_token(raw: str) -> bool:
    """Backwards-compatible alias of :func:`requires_authorization`."""
    return requires_authorization(raw)


def describe_command_policy() -> Mapping[str, Any]:
    """Introspection payload for ``GET /api/health`` and the Operator System page."""
    return {
        "control_commands": sorted(CONTROL_COMMANDS),
        "read_only_commands": sorted(READ_ONLY_COMMANDS),
        "halt_commands": sorted(HALT_COMMANDS),
        "resume_commands": sorted(RESUME_COMMANDS),
        "live_book_commands": sorted(LIVE_BOOK_COMMANDS),
        "config_commands": sorted(CONFIG_COMMANDS),
        "ai_commands": sorted(AI_COMMANDS),
    }
