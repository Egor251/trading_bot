"""One cost, one source.

`tools/edge_ceiling.py` hardcoded `cost_bps=10.0` at nineteen call sites while the
product charged `fee_bps: 5` + `slippage_bps: 2` = 7. Research ran 1.43x more
pessimistic than the system it documents.

The failure mode this guards is not the wrong number -- that was visible and got
fixed. It is the *next* divergence: nothing structurally prevented the two from
drifting apart again, and the drift would be silent because a research tool charging
its own cost is still producing plausible-looking output.

These assert the research tool reads the product's config rather than stating its
own, and that it does so at every call site.
"""

from __future__ import annotations

import re
from pathlib import Path

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

import pytest  # noqa: E402
import yaml  # noqa: E402

from engine.cost_calibration import (  # noqa: E402
    DEFAULT_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    cost_model_from_config,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "trading_bot.yaml"
TOOL = ROOT / "tools" / "edge_ceiling.py"


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_cost_model_reads_the_shipped_config():
    model = cost_model_from_config(_config())
    phantom = _config()["services"]["phantom_service"]
    assert model["fee_bps"] == float(phantom["fee_bps"])
    assert model["slippage_bps"] == float(phantom["slippage_bps"])
    assert model["total_bps"] == pytest.approx(
        float(phantom["fee_bps"]) + float(phantom["slippage_bps"])
    )


def test_absent_block_falls_back_to_a_stated_number_not_zero():
    """A missing cost block must not make execution look free."""
    model = cost_model_from_config(None)
    assert model["fee_bps"] == DEFAULT_FEE_BPS
    assert model["slippage_bps"] == DEFAULT_SLIPPAGE_BPS
    assert model["total_bps"] > 0.0

    partial = cost_model_from_config(
        {"services": {"phantom_service": {"fee_bps": 9.0}}}
    )
    assert partial["fee_bps"] == 9.0
    assert partial["slippage_bps"] == DEFAULT_SLIPPAGE_BPS


def test_the_block_itself_is_accepted_not_just_the_whole_config():
    """A caller that already read `services.phantom_service` passes the block.

    Without this, that caller gets the fallback -- which equals the shipped values,
    so a test comparing against the config still passes while the wiring is dead
    and every future config change is silently ignored. The values below differ from
    the defaults precisely so this cannot pass by coincidence.
    """
    block = {"fee_bps": 11.0, "slippage_bps": 3.0}
    from_block = cost_model_from_config(block)
    assert from_block["fee_bps"] == 11.0
    assert from_block["total_bps"] == pytest.approx(14.0)

    from_config = cost_model_from_config({"services": {"phantom_service": block}})
    assert from_config == from_block

    # A partial block must still read its own half rather than silently defaulting.
    partial = cost_model_from_config({"fee_bps": 11.0})
    assert partial["fee_bps"] == 11.0
    assert partial["slippage_bps"] == DEFAULT_SLIPPAGE_BPS


def test_both_config_locations_are_accepted():
    """The block is under `services`, but `trading` is where a reader looks first.

    Returning the fallback from a plausible-but-wrong path would be silent, so both
    are read rather than one being documented in a docstring nobody reads.
    """
    under_services = cost_model_from_config(
        {"services": {"phantom_service": {"fee_bps": 4.0, "slippage_bps": 1.0}}}
    )
    under_trading = cost_model_from_config(
        {"trading": {"phantom_service": {"fee_bps": 4.0, "slippage_bps": 1.0}}}
    )
    assert under_services["total_bps"] == 5.0
    assert under_trading["total_bps"] == 5.0


def test_research_tool_charges_the_same_cost_as_the_product():
    """The whole point, asserted end to end."""
    source = TOOL.read_text(encoding="utf-8")
    assert "_shipped_cost_bps()" in source
    assert "COST_BPS_PER_FILL = _shipped_cost_bps()" in source
    # And the config it reads really does carry a non-zero cost, so the wiring is
    # not accidentally reading an absent block and taking the fallback.
    assert cost_model_from_config(_config())["total_bps"] > 0.0


def test_no_call_site_hardcodes_a_cost_any_more():
    """`cost_bps=10.0` at nineteen sites is the exact regression this prevents."""
    source = TOOL.read_text(encoding="utf-8")
    hardcoded = re.findall(r"cost_bps\s*=\s*[\d.]+", source)
    assert hardcoded == [], f"hardcoded cost at {len(hardcoded)} site(s): {hardcoded}"


def test_no_call_site_charges_a_literal_but_variables_are_fine():
    """Loops that sweep a cost grid legitimately pass a variable.

    What must not exist is a number written inline, because that is a second,
    unlabelled source of truth -- and it is what the nineteen `cost_bps=10.0` were.
    """
    source = TOOL.read_text(encoding="utf-8")
    # Strip comments so prose about `cost_bps=` is not counted as a call site.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    call_sites = re.findall(r"cost_bps\s*=\s*([^\s,)]+)", code)
    assert call_sites, "expected the tool to pass cost_bps somewhere"
    for value in call_sites:
        assert not re.fullmatch(r"[\d.]+", value), f"inline cost literal: {value}"
    assert "COST_BPS_PER_FILL" in code