"""Quik transport Protocol — swap backends without QuikPy in the core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class QuikCandleRow:
    class_code: str
    sec_code: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    datetime: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


@runtime_checkable
class QuikTransport(Protocol):
    """
    Venue wire for QUIK terminal.

    Preferred backends (pick later via spike): Alex-Shur/quik_python,
    quik-lua-rpc + qlua. Do **not** treat cia76 QuikPy as canonical.
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def is_connected(self) -> bool: ...

    async def get_candles(
        self,
        class_code: str,
        sec_code: str,
        *,
        interval: int = 1,
        count: int = 1,
    ) -> list[QuikCandleRow]: ...

    async def send_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]: ...


class NullQuikTransport:
    """Placeholder until a real QUIK backend is selected and wired."""

    def __init__(self, reason: str = "quik_transport_not_configured") -> None:
        self.reason = reason
        self._connected = False

    async def connect(self) -> None:
        self._connected = False
        raise RuntimeError(self.reason)

    async def close(self) -> None:
        self._connected = False

    async def is_connected(self) -> bool:
        return False

    async def get_candles(
        self,
        class_code: str,
        sec_code: str,
        *,
        interval: int = 1,
        count: int = 1,
    ) -> list[QuikCandleRow]:
        raise RuntimeError(self.reason)

    async def send_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(self.reason)


class UnavailableQuikTransport(NullQuikTransport):
    """Explicit unavailable marker for status / health."""

    def __init__(self) -> None:
        super().__init__(
            reason=(
                "QUIK backend not wired — use NullQuikTransport replacement "
                "(quik_python or qlua/rpc); QuikPy is legacy-only"
            )
        )
