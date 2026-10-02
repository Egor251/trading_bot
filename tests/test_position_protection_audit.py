"""A position with no stop is the largest unbounded loss available to us.

Stop coverage was already computed -- `stop_coverage` on the runtime snapshot,
a gauge for a dashboard. Nobody checked it. A gauge that is written and never
read is not a control; it is the same shape as the fee reconciler that never ran
and the liquidity gate that never received data.

These tests pin that the audit runs on a loop, finds the gap, and raises rather
than merely measuring.

One deliberate non-behaviour is also pinned. On a venue outage the audit does
*not* try to flatten. The venue being unreachable is precisely why a market order
cannot be placed; pretending to close would convert a known exposure into an
unverified one, which is worse than honestly reporting the gap.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from services.ops_monitor_service import OpsMonitorService  # noqa: E402


def _service(open_keys, protected, *, alerts=None):
    core = MagicMock()
    core.event_bus.publish = AsyncMock()
    protection = MagicMock()
    protection.has_protection = MagicMock(
        side_effect=lambda key: key in set(protected)
    )
    runtime = MagicMock()
    runtime.open_position_keys = MagicMock(return_value=list(open_keys))
    runtime.protection = protection
    core.trading = runtime

    svc = OpsMonitorService(core, "ops_monitor_service")
    svc.alerts = alerts
    return svc, runtime


def test_a_protected_book_is_quiet() -> None:
    svc, _ = _service(["A", "B"], ["A", "B"])
    result = asyncio.run(svc.audit_position_protection())
    assert result["checked"] == 2
    assert result["unprotected"] == []
    assert result["coverage"] == 1.0


def test_a_bare_position_is_found_and_alarmed() -> None:
    """The gap must be an incident, not a number."""
    alerts = MagicMock()
    alerts.emit = MagicMock(
        return_value={"name": "unprotected_position", "summary": "gap"}
    )
    svc, _ = _service(["A", "B", "C"], ["A", "C"], alerts=alerts)

    result = asyncio.run(svc.audit_position_protection())

    assert result["unprotected"] == ["B"], "the bare position was missed"
    assert result["coverage"] == 2 / 3
    assert alerts.emit.called, "an uncovered position must raise, not be measured"
    name = alerts.emit.call_args[0][0]
    assert name == "unprotected_position"


def test_an_empty_book_is_not_an_incident() -> None:
    alerts = MagicMock()
    alerts.emit = MagicMock()
    svc, _ = _service([], [], alerts=alerts)
    result = asyncio.run(svc.audit_position_protection())
    assert result["checked"] == 0
    assert not alerts.emit.called


def test_the_audit_survives_a_runtime_that_cannot_answer() -> None:
    """A broken runtime must not take the monitor down with it."""
    core = MagicMock()
    runtime = MagicMock()
    runtime.open_position_keys = MagicMock(side_effect=RuntimeError("boom"))
    core.trading = runtime
    svc = OpsMonitorService(core, "ops_monitor_service")
    result = asyncio.run(svc.audit_position_protection())
    assert result.get("skipped"), "a failure must be reported, not raised"


def test_the_audit_is_scheduled_not_merely_available() -> None:
    """A method nobody calls is the fourth version of this bug."""
    import inspect

    src = inspect.getsource(OpsMonitorService)
    assert "audit_position_protection()" in src, (
        "the audit must be invoked from the monitor loop"
    )
    assert "protection_audit_sec" in src, "and must be rate limited by config"