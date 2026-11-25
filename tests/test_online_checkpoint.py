"""Checkpoint known-answer pins: round-trip, atomicity, corrupt input."""

from __future__ import annotations

import json

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.online_checkpoint import (  # noqa: E402
    Checkpoint,
    config_hash,
    load,
    save,
    weights_from_models,
)


def test_weights_are_flattened_to_json_lists():
    out = weights_from_models({"a": {"params": [[[1.0, 2.0]]]}})
    assert out == {"a": [[[1.0, 2.0]]]}


def test_round_trip_preserves_everything():
    import tempfile
    from pathlib import Path

    ckpt = Checkpoint(
        version=1,
        ts=123.5,
        observations=200,
        bars_seen=600,
        last_decision={"tradable": False, "observe_only": True},
        weights={"s0": [[[0.1, 0.2], [0.3, 0.4]]]},
        config_hash="abc123",
    )
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "weights.json"
        save(path, ckpt)
        assert not (path.with_suffix(path.suffix + ".tmp")).exists()
        got = load(path)
    assert got == ckpt


def test_corrupt_checkpoint_is_unavailable():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "weights.json"
        p.write_text(json.dumps({"version": 9}), encoding="utf-8")
        assert load(p) is None
        p.write_text("not json", encoding="utf-8")
        assert load(p) is None
        assert load(Path(d) / "missing.json") is None


def test_config_hash_changes_with_config():
    assert config_hash({"a": 1}) != config_hash({"a": 2})
