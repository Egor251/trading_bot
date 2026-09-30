#!/usr/bin/env python3
"""Start Engine (headless) + Operator UI together.

Usage (from repo root)::

    python run_operator.py              # Tauri desktop (primary)
    python run_operator.py --web        # FastAPI BFF + Next.js Operator
    python run_operator.py --desktop    # explicit Tauri desktop
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_BFF_PORT = 8787
DEFAULT_NEXT_PORT = 3000
DESKTOP_DIR = ROOT / "ui" / "desktop"
TAURI_DIR = DESKTOP_DIR / "src-tauri"


def _wait_port(host: str, port: int, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def _terminate(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _resolve_ui_mode(args: argparse.Namespace) -> str:
    if args.web:
        return "web"
    if args.desktop:
        return "desktop"
    if args.ui:
        return str(args.ui).strip().lower()
    env = str(os.environ.get("OPERATOR_UI") or "").strip().lower()
    if env in {"web", "desktop", "dpg", "tauri"}:
        if env in {"dpg", "tauri"}:
            return "desktop"
        return env
    return "desktop"


def _find_tauri_bin() -> Path | None:
    override = str(os.environ.get("OPERATOR_TAURI_BIN") or "").strip()
    if override:
        p = Path(override)
        return p if p.is_file() else None
    exe = "trading-bot-operator.exe" if sys.platform == "win32" else "trading-bot-operator"
    candidates = [
        TAURI_DIR / "target" / "release" / exe,
        TAURI_DIR / "target" / "debug" / exe,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _npm() -> str:
    return "npm.cmd" if sys.platform == "win32" else "npm"


def _ensure_desktop_frontend() -> int:
    """Build Next static export if missing."""
    out_dir = ROOT / "ui" / "web" / "out"
    if (out_dir / "index.html").is_file():
        return 0
    web = ROOT / "ui" / "web"
    print("building desktop frontend (Next static export)…", flush=True)
    r = subprocess.run([_npm(), "run", "build:desktop"], cwd=str(web))
    return int(r.returncode or 0)


def _start_tauri(env: dict[str, str], *, no_tray: bool, start_in_tray: bool) -> subprocess.Popen[bytes]:
    tauri_env = env.copy()
    if no_tray:
        tauri_env["OPERATOR_NO_TRAY"] = "1"
    if start_in_tray:
        tauri_env["OPERATOR_START_IN_TRAY"] = "1"

    bin_path = _find_tauri_bin()
    if bin_path is not None:
        print(f"tauri: {bin_path}", flush=True)
        return subprocess.Popen([str(bin_path)], cwd=str(ROOT), env=tauri_env)

    # Dev path: cargo tauri / npm run tauri
    npm = _npm()
    pkg = DESKTOP_DIR / "package.json"
    if pkg.is_file():
        # Ensure CLI present
        if not (DESKTOP_DIR / "node_modules" / "@tauri-apps" / "cli").exists():
            print("installing Tauri CLI (ui/desktop)…", flush=True)
            subprocess.run([npm, "install"], cwd=str(DESKTOP_DIR), check=False)
        cmd = [npm, "run", "dev"]
        print(f"tauri: {' '.join(cmd)} (cwd={DESKTOP_DIR})", flush=True)
        return subprocess.Popen(cmd, cwd=str(DESKTOP_DIR), env=tauri_env)

    cargo = shutil.which("cargo")
    if cargo:
        cmd = [cargo, "tauri", "dev"]
        print(f"tauri: {' '.join(cmd)} (cwd={TAURI_DIR})", flush=True)
        return subprocess.Popen(cmd, cwd=str(TAURI_DIR), env=tauri_env)

    raise FileNotFoundError(
        "Tauri Operator binary not found. Build with: "
        "cd ui/desktop && npm install && npm run build"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch trading_bot engine + Operator UI"
    )
    parser.add_argument("--engine-host", default=DEFAULT_HOST)
    parser.add_argument("--engine-port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--ui",
        choices=("desktop", "web"),
        default=None,
        help="Operator frontend (default: desktop Tauri; or OPERATOR_UI=desktop|web)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Next.js Operator + FastAPI BFF",
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Tauri desktop Operator (default)",
    )
    parser.add_argument("--ui-host", default=DEFAULT_HOST, help="BFF bind host")
    parser.add_argument(
        "--ui-port",
        type=int,
        default=DEFAULT_BFF_PORT,
        help="BFF port (FastAPI)",
    )
    parser.add_argument(
        "--next-port",
        type=int,
        default=DEFAULT_NEXT_PORT,
        help="Next.js port (--web)",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not auto-open the browser for --web",
    )
    parser.add_argument("--width", type=int, default=1400, help="Ignored (legacy)")
    parser.add_argument("--height", type=int, default=900, help="Ignored (legacy)")
    parser.add_argument(
        "--engine-wait",
        type=float,
        default=45.0,
        help="Seconds to wait for bridge port before starting UI",
    )
    parser.add_argument(
        "--kill-engine-on-ui-exit",
        action="store_true",
        help="Stop host/main.py when Operator UI exits (default: leave engine running)",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Desktop: allow window close to quit (disable hide-to-tray)",
    )
    parser.add_argument(
        "--start-in-tray",
        action="store_true",
        help="Desktop: start hidden in the system tray",
    )
    args = parser.parse_args()
    ui_mode = _resolve_ui_mode(args)

    os.chdir(ROOT)
    py = sys.executable
    host = str(args.engine_host)
    port = int(args.engine_port)

    engine_path = ROOT / "host" / "main.py"
    if not engine_path.is_file():
        print(f"ERROR: engine entry not found: {engine_path}", file=sys.stderr, flush=True)
        return 2

    engine_cmd = [py, str(engine_path), "--headless"]
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + prev if prev else "")

    print(f"engine: {' '.join(engine_cmd)}", flush=True)
    print(f"operator ui mode: {ui_mode}", flush=True)
    engine = subprocess.Popen(
        engine_cmd,
        cwd=str(ROOT),
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )

    ui: subprocess.Popen[bytes] | None = None
    next_proc: subprocess.Popen[bytes] | None = None
    interrupted = False
    try:
        print(f"waiting for bridge {host}:{port} …", flush=True)
        if not _wait_port(host, port, timeout=float(args.engine_wait)):
            print(
                f"ERROR: engine bridge did not open on {host}:{port} "
                f"(engine exit={engine.poll()})",
                file=sys.stderr,
                flush=True,
            )
            return 1

        bff_cmd = [
            py,
            "-m",
            "ui",
            "--web",
            "--engine-host",
            host,
            "--engine-port",
            str(port),
            "--host",
            str(args.ui_host),
            "--port",
            str(args.ui_port),
        ]
        print(f"bff: {' '.join(bff_cmd)}", flush=True)
        ui = subprocess.Popen(bff_cmd, cwd=str(ROOT), env=env)

        if not _wait_port(str(args.ui_host), int(args.ui_port), timeout=30.0):
            print(
                f"ERROR: BFF did not open on {args.ui_host}:{args.ui_port}",
                file=sys.stderr,
                flush=True,
            )
            return 1

        if ui_mode == "web":
            web_dir = ROOT / "ui" / "web"
            if not (web_dir / "package.json").is_file():
                print(
                    f"ERROR: Next.js app missing at {web_dir}",
                    file=sys.stderr,
                    flush=True,
                )
                return 1

            next_env = env.copy()
            next_env["NEXT_PUBLIC_PLATFORM"] = "web"
            next_env["NEXT_PUBLIC_API_URL"] = f"http://{args.ui_host}:{args.ui_port}"
            next_env["NEXT_PUBLIC_WS_URL"] = f"ws://{args.ui_host}:{args.ui_port}/ws"
            next_cmd = [
                _npm(),
                "run",
                "dev",
                "--",
                "-H",
                "127.0.0.1",
                "-p",
                str(args.next_port),
            ]
            print(f"next: {' '.join(next_cmd)} (cwd={web_dir})", flush=True)
            next_proc = subprocess.Popen(next_cmd, cwd=str(web_dir), env=next_env)

            if not args.no_open_browser:
                url = f"http://127.0.0.1:{args.next_port}"
                time.sleep(1.5)
                try:
                    webbrowser.open(url)
                    print(f"opened browser {url}", flush=True)
                except Exception as exc:
                    print(f"open browser failed ({exc}); open {url} manually", flush=True)

            while True:
                if ui.poll() is not None:
                    return int(ui.returncode or 0)
                if next_proc.poll() is not None:
                    return int(next_proc.returncode or 0)
                time.sleep(0.4)

        # Desktop — Tauri
        code = _ensure_desktop_frontend()
        if code != 0:
            print("ERROR: desktop frontend build failed", file=sys.stderr, flush=True)
            return code
        try:
            next_proc = _start_tauri(
                env, no_tray=bool(args.no_tray), start_in_tray=bool(args.start_in_tray)
            )
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr, flush=True)
            return 2

        while True:
            if ui.poll() is not None:
                return int(ui.returncode or 0)
            if next_proc.poll() is not None:
                return int(next_proc.returncode or 0)
            time.sleep(0.4)
    except KeyboardInterrupt:
        interrupted = True
        print("\nshutting down…", flush=True)
        return 130
    finally:
        _terminate(next_proc)
        _terminate(ui)
        if interrupted or args.kill_engine_on_ui_exit or engine.poll() is not None:
            _terminate(engine)
        elif engine.poll() is None:
            reopen = (
                f"{py} run_operator.py --web"
                if ui_mode == "web"
                else f"{py} run_operator.py --desktop"
            )
            print(
                f"engine left running pid={engine.pid} on {host}:{port} "
                f"(reopen UI: {reopen})",
                flush=True,
            )


if __name__ == "__main__":
    raise SystemExit(main())
