"""Ensure sibling Code_base packages are importable during pytest collection."""

from __future__ import annotations

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()
