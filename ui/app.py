"""Operator web BFF — FastAPI bridge to Engine (Tauri / Next.js talk here)."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ui.bridge_client import EngineBridgeClient

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

# Next.js / Tauri / static preview origins
_CORS_ORIGINS = (
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
    "http://127.0.0.1:8787",
    "http://localhost:8787",
    "https://tauri.localhost",
    "http://tauri.localhost",
    "tauri://localhost",
)


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.bridge = EngineBridgeClient(on_snapshot=self.fanout)

    async def fanout(self, snap: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_json(snap)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await hub.bridge.start()
    yield
    await hub.bridge.stop()


app = FastAPI(title="trading_bot Operator BFF", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_CORS_ORIGINS),
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class CmdBody(BaseModel):
    cmd: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout: float = 30.0


@app.get("/", response_class=HTMLResponse)
async def index() -> Any:
    """Legacy static cockpit; Next.js Operator is preferred on :3000."""
    if (STATIC / "index.html").is_file():
        return FileResponse(STATIC / "index.html")
    return RedirectResponse(url="http://127.0.0.1:3000/")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "engine_connected": hub.bridge.connected,
        "engine_error": hub.bridge.last_error,
        "browser_clients": len(hub.clients),
    }


@app.get("/api/snapshot")
async def snapshot() -> dict[str, Any]:
    return hub.bridge.last_snapshot or {"schema": "ui.snapshot.v1", "empty": True}


@app.post("/api/command/{cmd}")
async def command_path(cmd: str) -> dict[str, Any]:
    """Fire-and-forget simple commands (KILL / RESUME)."""
    try:
        await hub.bridge.send_command({"cmd": cmd.upper()})
        return {"ok": True, "cmd": cmd.upper()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.post("/api/cmd")
async def command_json(body: CmdBody) -> dict[str, Any]:
    """JSON command with optional ack wait (preferred by Next.js)."""
    raw = str(body.cmd or "").strip().upper()
    if not raw:
        return {"ok": False, "error": "missing_cmd"}
    payload = dict(body.payload or {})
    payload["cmd"] = raw
    timeout = max(1.0, min(600.0, float(body.timeout or 30.0)))
    # Long-running: ASK / phantom / walk-forward
    if raw in {"ASK", "PHANTOM_RUN"}:
        timeout = max(timeout, 120.0)
    if raw == "PHANTOM_WALK_FORWARD":
        timeout = max(timeout, 300.0)
    try:
        ack = await hub.bridge.request(payload, timeout=timeout)
        if isinstance(ack, dict):
            out = dict(ack)
            out.pop("schema", None)
            out.setdefault("cmd", raw)
            if "ok" not in out:
                out["ok"] = "error" not in out
            return out
        return {"ok": True, "cmd": raw, "ack": ack}
    except Exception as exc:  # noqa: BLE001
        # Fallback: fire-and-forget if request unsupported / not connected waiters
        try:
            await hub.bridge.send_command(payload)
            return {"ok": True, "cmd": raw, "acked": False, "note": str(exc)}
        except Exception as exc2:  # noqa: BLE001
            return {"ok": False, "cmd": raw, "error": str(exc2)}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    hub.clients.add(ws)
    if hub.bridge.last_snapshot:
        await ws.send_json(hub.bridge.last_snapshot)
    try:
        while True:
            raw = await ws.receive_json()
            if not isinstance(raw, dict):
                continue
            cmd = raw.get("cmd") or raw.get("action")
            if cmd:
                try:
                    await hub.bridge.send_command(raw)
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)


def main() -> None:
    parser = argparse.ArgumentParser(description="trading_bot Operator web BFF")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--engine-host", default="127.0.0.1")
    parser.add_argument("--engine-port", type=int, default=8765)
    args = parser.parse_args()
    hub.bridge.host = args.engine_host
    hub.bridge.port = args.engine_port

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
