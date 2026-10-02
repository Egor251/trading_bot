"""Operator web BFF — FastAPI bridge to Engine (Tauri / Next.js talk here).

Security posture
----------------
This process can halt, re-arm and mutate a live trading book, so it is
authenticated independently of the engine's HMAC command tokens:

* every ``/api/*`` call except the bootstrap endpoints must present
  ``X-Operator-Token`` (or ``Authorization: Bearer``);
* CORS and the WebSocket handshake match ``Origin`` against an **exact**
  allow-list — a regex over ``localhost(:\\d+)?`` would trust every other dev
  server on the machine;
* mutating endpoints require a JSON content type, so a cross-site form post
  cannot reach them;
* a command is **never** re-sent after an ack timeout.  The old fallback
  re-dispatched ``PLACE_ORDER``/``CLOSE_ALL``/``RESET_PAPER`` and answered
  ``{"ok": true}``, which doubled real side effects while telling the operator
  the kill switch had worked.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine.operator_session import (
    TOKEN_HEADER,
    extract_bearer,
    get_session,
    token_from_query,
)
from ui.bridge_client import EngineBridgeClient

_log = logging.getLogger("operator_bff")

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

_MAX_WS_CLIENTS = 16
_SEND_TIMEOUT = 5.0


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.bridge = EngineBridgeClient(on_snapshot=self.fanout)

    async def fanout(self, snap: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            # Bound the fan-out: one stalled browser must not block the loop.
            try:
                await asyncio.wait_for(ws.send_json(snap), timeout=_SEND_TIMEOUT)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    session = get_session()
    _log.info(
        "operator session ready token_source=%s allowed_origins=%s",
        session.status()["token_source"],
        ",".join(session.allowed_origins),
    )
    if session.generated:
        _log.warning(
            "OPERATOR_API_TOKEN is not set; generated a session token at %s. "
            "Any process running as this user can read it — that is accepted for "
            "a single-user desktop tool, but do not expose this port.",
            session.status()["token_path"],
        )
    await hub.bridge.start()
    yield
    await hub.bridge.stop()


app = FastAPI(title="trading_bot Operator BFF", lifespan=lifespan)
# Exact origins only.  ``allow_credentials`` + an origin regex previously made
# every localhost port a trusted peer.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_session().allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", TOKEN_HEADER, "Authorization"],
)

if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", ""}


def _require_token(
    x_operator_token: str | None = Header(default=None, alias=TOKEN_HEADER),
    authorization: str | None = Header(default=None),
) -> None:
    session = get_session()
    if session.verify(x_operator_token) or session.verify(extract_bearer(authorization)):
        return
    raise HTTPException(status_code=401, detail="operator token required")


def _require_json(request: Request) -> None:
    """Reject non-JSON bodies so cross-site form posts cannot mutate state."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    if content_type and content_type != "application/json":
        raise HTTPException(status_code=415, detail="application/json required")


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


@app.get("/api/session")
async def session_bootstrap(request: Request, origin: str | None = None) -> Any:
    """Hand the session token to a *first-party* origin.

    Gated by the exact-origin allow-list, so a hostile page cannot bootstrap a
    token; the Operator app calls this once at start-up and caches the value.
    """
    session = get_session()
    supplied = request.headers.get("origin") or origin
    if not session.origin_allowed(supplied):
        raise HTTPException(status_code=403, detail="origin not allowed")
    return {"token": session.token, "expires_in": 0, "origin": supplied or ""}


@app.get("/api/health")
async def health() -> JSONResponse:
    """Honest liveness: green only when the engine is connected and trading.

    The previous version returned a hard-coded ``ok: True``, which meant a
    container probe stayed healthy through a dead engine, a dead feed and a
    halted trading book.  This is the only health signal in the product, so it
    has to reflect reality.
    """
    snap = hub.bridge.last_snapshot or {}
    trading = snap.get("trading") if isinstance(snap.get("trading"), dict) else {}
    halted = bool(snap.get("halted") or trading.get("halted"))
    feed_age = trading.get("feed_age_seconds")
    connected = hub.bridge.connected
    ok = bool(connected and not halted)
    body = {
        "ok": ok,
        "engine_connected": connected,
        "engine_error": hub.bridge.last_error,
        "halted": halted,
        "halt_reason": snap.get("halt_reason") or trading.get("halt_reason") or "",
        "mode": trading.get("mode") or snap.get("mode") or "",
        "feed_age_seconds": feed_age,
        "browser_clients": len(hub.clients),
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/metrics")
async def metrics() -> Any:
    """Prometheus exposition relayed from the engine.

    This endpoint is what makes `ops/prometheus/alerts.yml` reachable. The
    exporter and all ten metric names already existed, but nothing imported the
    exporter and no process served the text format — so the alerts could never
    fire and a dead feed, a disconnected broker, a reconciliation break or a
    drawdown breach were all silent.
    """
    from fastapi.responses import PlainTextResponse

    snap = hub.bridge.last_snapshot or {}
    body = snap.get("metrics_text")
    if not isinstance(body, str) or not body.strip():
        body = "# engine has not published a metrics snapshot yet\n"
    return PlainTextResponse(
        body, media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@app.get("/api/snapshot")
async def snapshot(
    x_operator_token: str | None = Header(default=None, alias=TOKEN_HEADER),
    authorization: str | None = Header(default=None),
) -> Any:
    _require_token(x_operator_token, authorization)
    return hub.bridge.last_snapshot or {"schema": "ui.snapshot.v1", "empty": True}


@app.post("/api/command/{cmd}")
async def command_path(
    cmd: str,
    request: Request,
    x_operator_token: str | None = Header(default=None, alias=TOKEN_HEADER),
    authorization: str | None = Header(default=None),
) -> Any:
    """Simple commands (KILL / RESUME) — always awaited, never fire-and-forget."""
    _require_token(x_operator_token, authorization)
    _require_json(request)
    raw = str(cmd or "").strip().upper()
    try:
        ack = await hub.bridge.request({"cmd": raw}, timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        # Report the failure instead of pretending the command landed.
        return JSONResponse(
            {"ok": False, "cmd": raw, "error": str(exc), "acked": False},
            status_code=504,
        )
    out = dict(ack) if isinstance(ack, dict) else {"ack": ack, "ok": True}
    out.setdefault("cmd", raw)
    out.setdefault("ok", "error" not in out)
    return out


@app.post("/api/cmd")
async def command_json(
    body: CmdBody,
    request: Request,
    x_operator_token: str | None = Header(default=None, alias=TOKEN_HEADER),
    authorization: str | None = Header(default=None),
) -> Any:
    """JSON command with ack wait (preferred by Next.js)."""
    _require_token(x_operator_token, authorization)
    _require_json(request)
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
    except Exception as exc:  # noqa: BLE001
        # A timeout means "unknown outcome", not "failed".  Never re-send: the
        # command may already have executed at the engine.
        _log.warning("cmd %s unacknowledged: %s", raw, exc)
        return JSONResponse(
            {
                "ok": False,
                "cmd": raw,
                "acked": False,
                "timed_out": True,
                "error": str(exc),
                "note": "outcome unknown; reconcile via /api/snapshot, do not retry blindly",
            },
            status_code=504,
        )
    if isinstance(ack, dict):
        out = dict(ack)
        out.pop("schema", None)
        out.setdefault("cmd", raw)
        if "ok" not in out:
            out["ok"] = "error" not in out
        return out
    return {"ok": True, "cmd": raw, "ack": ack}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    session = get_session()
    origin = ws.headers.get("origin")
    if not session.origin_allowed(origin):
        await ws.close(code=1008)
        return
    supplied = (
        ws.headers.get(TOKEN_HEADER)
        or extract_bearer(ws.headers.get("authorization"))
        or token_from_query(ws.query_params.query)
    )
    if not session.verify(supplied):
        await ws.close(code=1008)
        return
    await ws.accept()
    if len(hub.clients) >= _MAX_WS_CLIENTS:
        await ws.close(code=1013)
        return
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
    parser.add_argument(
        "--allow-lan",
        action="store_true",
        help="permit a non-loopback bind address (see the startup warning)",
    )
    args = parser.parse_args()
    if not _is_loopback(args.host) and not args.allow_lan:
        parser.error(
            f"--host {args.host} is not loopback. This BFF can halt and mutate a "
            "live trading book; pass --allow-lan only behind an authenticated "
            "reverse proxy / VPN."
        )
    if not _is_loopback(args.host):
        _log.critical(
            "BFF bound to %s: the control plane is reachable off-host. "
            "Ensure a TLS terminator and network policy are in place.",
            args.host,
        )
    hub.bridge.host = args.engine_host
    hub.bridge.port = args.engine_port

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
