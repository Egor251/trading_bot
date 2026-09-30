# trading_bot Operator (Tauri)

Desktop shell for the Operator UI. Frontend is the **static Next export** from [`../web`](../web) (`npm run build:desktop` → `../web/out`).

## Prerequisites

- Rust + Cargo (**MSVC** toolchain on Windows: `stable-x86_64-pc-windows-msvc`)
- Visual Studio Build Tools with C++ workload (`link.exe` / `cl.exe`)
- Node.js + npm
- WebView2 (Windows)

GNU/`mingw` often fails linking Tauri with `export ordinal too large`. This crate pins MSVC via `src-tauri/rust-toolchain.toml`.

To keep CPU load down during builds:

```bash
set CARGO_BUILD_JOBS=2
cargo build --bins -j 2
```

## How rendering works

Tauri draws UI in its **own window** via **WebView2** (Chromium engine from Edge,
embedded — not the Edge browser app). Error pages can look like Edge; that is
still the Operator window.

- **Normal / `run_operator.py`:** binary loads static files from `ui/web/out`
  (asset protocol). No `127.0.0.1:1420`, no system browser.
- **`npm run dev` / `tauri dev`:** temporarily serves `out/` on `:1420` for hot
  reload. Still a Tauri window, not Edge.

`webbrowser.open` is only used for `python run_operator.py --web`.

## Dev

```bash
# Preferred: engine + BFF + Tauri binary (embedded static UI)
python run_operator.py

# Or pieces:
python host/main.py --headless
python -m ui --web
# then start the built exe under src-tauri/target/debug/
```

## Build

```bash
cd ui/web && npm run build:desktop
cd ../desktop && npm install && npm run build
```

Binary: `src-tauri/target/release/trading-bot-operator` (`.exe` on Windows).

## Native behaviour

- System tray: Open / Exit
- Close window → hide to tray (`OPERATOR_NO_TRAY=1` disables)
- `OPERATOR_START_IN_TRAY=1` starts hidden
- OS notifications via `@tauri-apps/plugin-notification` (React `DesktopChrome`)
