"""Pin the known-answer fix in the existential-strategy gate.

A strategy that loses money cannot be counted as profitable even if it beats
a deeply negative random-timing null. The first run reported 15 gated strategies
in a window with ZERO positive nets; that contradiction was the instrument
bug, not a result.
"""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from tools.existential_strategy import beats_null_and_profitable  # noqa: E402


def test_a_losing_strategy_never_gates_even_above_a_negative_null():
    assert beats_null_and_profitable(-5.0, -10.0) is False
    assert beats_null_and_profitable(0.0, -10.0) is False


def test_a_profitable_strategy_must_still_beat_the_null():
    assert beats_null_and_profitable(3.0, 1.0) is True
    assert beats_null_and_profitable(3.0, 5.0) is False
