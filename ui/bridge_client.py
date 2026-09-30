"""TCP JSON-lines client to Engine ``ui_bridge_service``."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ui.control_auth_client import attach_control_auth

# Seeded OHLC for a few symbols easily exceeds asyncio's default 64 KiB limit.
_BRIDGE_READ_LIMIT = 8 * 1024 * 1024


class EngineBridgeClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        on_snapshot: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        on_ack: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_snapshot = on_snapshot
        self.on_ack = on_ack
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task[None] | None = None
        self.connected = False
        self.last_snapshot: dict[str, Any] = {}
        self.last_error: str = ""
        self._ack_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        for fut in list(self._ack_waiters.values()):
            if not fut.done():
                fut.cancel()
        self._ack_waiters.clear()
        await self._close()

    async def send_command(self, cmd: str | dict[str, Any]) -> None:
        if self._writer is None:
            raise RuntimeError("not connected to engine bridge")
        if isinstance(cmd, str):
            payload = {"cmd": cmd}
        else:
            payload = dict(cmd)
        payload = attach_control_auth(payload)
        self._writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._writer.drain()

    async def request(
        self, cmd: str | dict[str, Any], *, timeout: float = 5.0
    ) -> dict[str, Any]:
        if self._writer is None:
            raise RuntimeError("not connected to engine bridge")
        if isinstance(cmd, str):
            payload: dict[str, Any] = {"cmd": cmd}
        else:
            payload = dict(cmd)
        # Auth + request_id applied once in send_command / here.
        rid = str(payload.get("request_id") or uuid.uuid4())
        payload["request_id"] = rid
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._ack_waiters[rid] = fut
        try:
            await self.send_command(payload)
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            raise
        finally:
            self._ack_waiters.pop(rid, None)

    async def _run(self) -> None:
        while True:
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    self.host, self.port, limit=_BRIDGE_READ_LIMIT
                )
                self.connected = True
                self.last_error = ""
                assert self._reader is not None
                while True:
                    line = await self._reader.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if data.get("schema") == "ui.ack.v1":
                        rid = str(data.get("request_id") or "")
                        fut = self._ack_waiters.get(rid) if rid else None
                        if fut is not None and not fut.done():
                            fut.set_result(data)
                        elif self.on_ack:
                            # fire-and-forget acks only (request() waiters skip on_ack)
                            result = self.on_ack(data)
                            if asyncio.iscoroutine(result):
                                await result
                        continue
                    self.last_snapshot = data
                    if self.on_snapshot:
                        result = self.on_snapshot(data)
                        if asyncio.iscoroutine(result):
                            await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self.connected = False
                await self._close()
                await asyncio.sleep(1.0)

    async def _close(self) -> None:
        self.connected = False
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = self._writer = None
