# Operator UI (Stage 5)

**Primary:** Tauri desktop Operator (`ui/desktop`, static Next export from `ui/web`).  
**Secondary:** Next.js web Operator in the browser (`python run_operator.py --web`).

## Architecture

```
Engine  ui_bridge_service  ──TCP JSON-lines :8765──►  FastAPI BFF :8787
                                                         ▲
                              ┌──────────────────────────┤
                              │                          │
                     Tauri webview                 Next.js :3000
                     (static `ui/web/out`)         (dev / web mode)
                     REST /api + WS /ws            `/backend` rewrite
```

Rules:
- GUI **never** inside the Engine process.
- UI crash must not stop trading.
- Kill Switch → `system.halt` / `system.resume`.
- AI Analyst asks via `ASK` — analyst does **not** place orders.
- Snapshots redact API keys/tokens; provider secrets are resolved from environment variables.

## Platforms

| | Desktop (Tauri) | Web |
|--|-----------------|-----|
| Build | `NEXT_PUBLIC_PLATFORM=desktop` → `output: 'export'` | `NEXT_PUBLIC_PLATFORM=web` |
| API | absolute `http://127.0.0.1:8787` | `/backend/*` rewrite |
| Native | tray, close→tray, OS notifications | browser only |
| Flag | `NEXT_PUBLIC_PLATFORM=desktop` | `NEXT_PUBLIC_PLATFORM=web` |

Shared React pages live under `ui/web`. Desktop-only chrome: `DesktopChrome`, Tauri plugins.

## Information architecture (operator roles)

| Page | Purpose |
|------|---------|
| **Desk** | Live watch: status, book, consilium decision, votes, tape, exposures, Kill |
| **Book** | Universe / exchange / instruments (checkbox active set) |
| **Consilium** | Strategy list, weights, params + vote table |
| **Phantom** | Sandbox: run / grid / walk-forward / capture |
| **Strategy** | Live/observe/off roles + params |
| **Charts** | OHLC + equity |
| **Costs** | Fee schedule draft / validate / activate |
| **System** | Market / Risk / AI / Storage / Extensions |

Brand in UI: `trading_bot` / `Operator`.

## Commands

| cmd | Effect |
|-----|--------|
| `KILL` / `RESUME` | halt / resume |
| `SET_UNIVERSE` | active book + mode |
| `SET_TRADING` | mode / equity / risk / market services |
| `SET_ENSEMBLE` | strategies / weights |
| `SET_PLUGIN` | enable + start/stop |
| `SET_STRATEGY_PARAMS` | per-strategy params |
| `PHANTOM_RUN` | grid/optimize/patterns |
| `PHANTOM_RESET` | clear shadow fills / last grid |
| `ASK` | AI analyst question |
| `SET_AI` | AI provider / profiles |
| `SET_STORAGE` | hot reroute / enable backends |
| `SAVE_CONFIG` | persist YAML |

## Run

```bash
# Desktop (primary): Engine + BFF + Tauri
python run_operator.py

# Web: Engine + BFF + Next dev
python run_operator.py --web

# Pieces separately
python host/main.py --headless
python -m ui --web
cd ui/desktop && npm install && npm run dev   # needs ui/web/out
```

Build desktop frontend + Tauri binary:

```bash
cd ui/web && npm run build:desktop
cd ../desktop && npm install && npm run build
```

## Depth backlog (post-cutover)

Former Dear PyGui Operator had denser UX. Port next (use git history of `ui/dpg/` as reference only):

1. Desk terminal layout (DOM / denser panes)
2. Phantom schema params, timeline scrub, chart
3. Strategy roster / dossier / viz
4. i18n (`ui/i18n`) wired into React
5. System Notify settings tab
