"""Entry: python -m ui — FastAPI BFF for Operator (Tauri desktop / Next web)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host.bootstrap import ensure_code_base_path  # noqa: E402

ensure_code_base_path()


def main() -> None:
    parser = argparse.ArgumentParser(description="trading_bot Operator BFF")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Start FastAPI BFF (default; required for Tauri / Next Operator)",
    )
    parser.add_argument(
        "--web-api",
        action="store_true",
        help="Alias for --web",
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Deprecated: Dear PyGui removed — use run_operator.py (Tauri)",
    )
    parser.add_argument("--engine-host", default="127.0.0.1")
    parser.add_argument("--engine-port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1", help="BFF bind host")
    parser.add_argument("--port", type=int, default=8787, help="BFF bind port")
    parser.add_argument("--width", type=int, default=1400, help="Ignored (legacy)")
    parser.add_argument("--height", type=int, default=900, help="Ignored (legacy)")
    parser.add_argument("--no-tray", action="store_true", help="Ignored (legacy)")
    parser.add_argument("--start-in-tray", action="store_true", help="Ignored (legacy)")
    args, _unknown = parser.parse_known_args()

    if args.desktop and not (args.web or args.web_api):
        print(
            "ERROR: Dear PyGui desktop Operator was removed.\n"
            "  Use:  python run_operator.py            # Tauri + BFF + engine\n"
            "  Or:   python run_operator.py --web       # browser Next + BFF\n"
            "  BFF:  python -m ui --web",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)

    from ui.app import hub, main as web_main

    hub.bridge.host = args.engine_host
    hub.bridge.port = args.engine_port
    print(
        f"Operator BFF on http://{args.host}:{args.port} "
        f"(engine {args.engine_host}:{args.engine_port})",
        flush=True,
    )
    sys.argv = [
        "ui.app",
        "--host",
        str(args.host),
        "--port",
        str(args.port),
        "--engine-host",
        str(args.engine_host),
        "--engine-port",
        str(args.engine_port),
    ]
    web_main()


if __name__ == "__main__":
    main()
