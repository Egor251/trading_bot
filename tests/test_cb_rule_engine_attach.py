"""cb_rule_engine is optional; microkernel boots without it."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()

from cb_rule_engine import RuleEngine  # noqa: E402
from microkernel_core import CoreSettings, MicrokernelCore  # noqa: E402


async def _boot_with_rules() -> None:
    cfg = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    cfg.write(
        "core:\n  name: t\nrules:\n  enabled: true\n  path: data/rules\n"
        "event_bus:\n  max_queue_size: 100\nplugins:\n  paths: []\n"
        "services:\n  exclude: [base_service]\n"
    )
    cfg.close()
    # empty services dir
    svc = tempfile.mkdtemp()
    core = MicrokernelCore(
        CoreSettings(config_path=cfg.name, services_dir=Path(svc), attach_rule_engine=True)
    )
    await core.boot()
    assert core.rule_engine is not None
    assert isinstance(core.rule_engine, RuleEngine)
    await core.shutdown()
    Path(cfg.name).unlink(missing_ok=True)


def test_boot_attaches_cb_rule_engine() -> None:
    asyncio.run(_boot_with_rules())


if __name__ == "__main__":
    test_boot_attaches_cb_rule_engine()
    print("cb_rule_engine attach OK")
