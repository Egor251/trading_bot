"""Swappable QUIK backends implementing ``QuikTransport``."""

from __future__ import annotations

from typing import Any

from engine.quik_transport import (
    NullQuikTransport,
    QuikTransport,
    UnavailableQuikTransport,
)


def build_quik_transport(
    kind: str | None = "null",
    **options: Any,
) -> QuikTransport:
    """
    Factory for QUIK wire backends.

    Kinds:
      - ``null`` / ``unavailable`` — explicit not configured
      - ``fake`` — deterministic local candles (tests / paper without terminal)
      - ``quik_python`` — Alex-Shur async client (preferred live spike)
      - ``qlua_rpc`` — Enfernuz quik-lua-rpc via ``qlua`` (alternate)
    """
    name = str(kind or "null").strip().lower()
    if name in {"", "null", "none", "unavailable"}:
        if name == "unavailable":
            return UnavailableQuikTransport()
        reason = str(options.get("reason") or "quik_transport_not_configured")
        return NullQuikTransport(reason=reason)

    if name in {"fake", "stub", "mock"}:
        from engine.quik_transports.fake import FakeQuikTransport

        return FakeQuikTransport(
            base_prices=dict(options.get("base_prices") or {}),
            default_price=float(options.get("default_price") or 100.0),
        )

    if name in {"quik_python", "quik-python", "alex_shur"}:
        from engine.quik_transports.quik_python_backend import QuikPythonTransport

        return QuikPythonTransport(
            host=str(options.get("host") or "127.0.0.1"),
            requests_port=int(options.get("requests_port") or 34130),
            callbacks_port=int(options.get("callbacks_port") or 34131),
        )

    if name in {"qlua_rpc", "qlua", "lua_rpc"}:
        from engine.quik_transports.qlua_rpc_backend import QluaRpcTransport

        return QluaRpcTransport(
            host=str(options.get("host") or "127.0.0.1"),
            port=int(options.get("port") or 5560),
        )

    raise ValueError(
        f"unknown quik transport kind={kind!r}; "
        "use null|fake|quik_python|qlua_rpc"
    )


__all__ = ["build_quik_transport"]
