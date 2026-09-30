"""Enfernuz quik-lua-rpc via optional ``qlua`` package."""

from __future__ import annotations

import asyncio
from typing import Any

from engine.quik_transport import QuikCandleRow


def _import_qlua() -> Any:
    try:
        import qlua  # type: ignore[import-untyped]

        return qlua
    except ImportError as exc:
        raise ImportError(
            "qlua not installed — pip install qlua "
            "(client for Enfernuz/quik-lua-rpc). QuikPy is not supported here."
        ) from exc


class QluaRpcTransport:
    """
    Alternate live QUIK backend: ZMQ/protobuf RPC service in the terminal.

    Exact client surface varies by ``qlua`` version — this wrapper uses
    duck-typed calls and ``asyncio.to_thread`` when the API is sync.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 5560) -> None:
        self.host = host
        self.port = port
        self._client: Any = None
        self._connected = False

    @property
    def available(self) -> bool:
        try:
            _import_qlua()
            return True
        except ImportError:
            return False

    async def connect(self) -> None:
        qlua = _import_qlua()
        # Common patterns across qlua versions
        if hasattr(qlua, "QluaClient"):
            self._client = qlua.QluaClient(host=self.host, port=self.port)
        elif hasattr(qlua, "Client"):
            self._client = qlua.Client(f"tcp://{self.host}:{self.port}")
        elif hasattr(qlua, "connect"):
            maybe = qlua.connect(self.host, self.port)
            self._client = await maybe if hasattr(maybe, "__await__") else maybe
        else:
            raise RuntimeError(
                "qlua package found but no known Client entrypoint — "
                "check qlua docs for this version"
            )
        if hasattr(self._client, "connect"):
            maybe = self._client.connect()
            if hasattr(maybe, "__await__"):
                await maybe
        self._connected = True

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            maybe = self._client.close()
            if hasattr(maybe, "__await__"):
                await maybe
        self._client = None
        self._connected = False

    async def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def get_candles(
        self,
        class_code: str,
        sec_code: str,
        *,
        interval: int = 1,
        count: int = 1,
    ) -> list[QuikCandleRow]:
        if self._client is None:
            raise RuntimeError("qlua_rpc_not_connected")
        # Prefer getCandles / get_candles_from_datasource style RPC names
        for name in (
            "get_candles",
            "getCandles",
            "get_candles_from_data_source",
            "GetCandlesFromDataSource",
        ):
            fn = getattr(self._client, name, None)
            if callable(fn):
                result = await self._call(fn, class_code, sec_code, interval, count)
                rows = result.get("data", result) if isinstance(result, dict) else result
                return [_normalize_row(class_code, sec_code, r) for r in (rows or [])]
        raise RuntimeError(
            "qlua client has no get_candles* method — wire per installed API"
        )

    async def send_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("qlua_rpc_not_connected")
        for name in ("send_transaction", "sendTransaction", "SendTransaction"):
            fn = getattr(self._client, name, None)
            if callable(fn):
                result = await self._call(fn, transaction)
                return result if isinstance(result, dict) else {"data": result}
        raise RuntimeError("qlua client has no send_transaction")

    async def _call(self, fn: Any, *args: Any) -> Any:
        maybe = fn(*args)
        if hasattr(maybe, "__await__"):
            return await maybe
        return await asyncio.to_thread(lambda: maybe)


def _normalize_row(class_code: str, sec_code: str, r: Any) -> QuikCandleRow:
    if isinstance(r, dict):
        dt = r.get("datetime") or r.get("dt")
        return QuikCandleRow(
            class_code=class_code,
            sec_code=sec_code,
            open=float(r.get("open", 0)),
            high=float(r.get("high", 0)),
            low=float(r.get("low", 0)),
            close=float(r.get("close", 0)),
            volume=float(r.get("volume", r.get("vol", 0)) or 0),
            datetime=dt if isinstance(dt, dict) else None,
            raw=r,
        )
    return QuikCandleRow(
        class_code=class_code,
        sec_code=sec_code,
        open=float(getattr(r, "open", 0)),
        high=float(getattr(r, "high", 0)),
        low=float(getattr(r, "low", 0)),
        close=float(getattr(r, "close", 0)),
        volume=float(getattr(r, "volume", 0) or 0),
        raw=None,
    )
