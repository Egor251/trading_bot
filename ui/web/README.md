# Operator Web (Next.js)

Operator UI for `trading_bot`. **Desktop primary is Tauri** (`ui/desktop`) loading a **static export** of this app. Browser / `next dev` is the web variant.

Stack: Next.js App Router + React 19 + Tailwind v4.

## Platforms

| Script | Platform | Notes |
|--------|----------|--------|
| `npm run build:desktop` | `NEXT_PUBLIC_PLATFORM=desktop` | `output: 'export'` → `out/` for Tauri |
| `npm run build:web` / `npm run dev` | `web` | `/backend` rewrite → BFF |
| `npm run serve:desktop` | — | serve `out/` on `:1420` for Tauri `devUrl` |

On desktop builds, API/WS use absolute BFF URLs (`NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL`). Web uses Next rewrites.

## Dev (browser)

```bash
# terminal 1 — engine
python host/main.py --headless

# terminal 2 — FastAPI BFF (:8787)
python -m ui --web

# terminal 3 — Next (:3000)
cd ui/web
npm install
npm run dev
```

Or together:

```bash
python run_operator.py --web
```

## Desktop (Tauri)

```bash
python run_operator.py
# or
cd ui/web && npm run build:desktop
cd ../desktop && npm install && npm run dev
```

Env:

- `NEXT_PUBLIC_API_URL` — default `http://127.0.0.1:8787`
- `NEXT_PUBLIC_WS_URL` — default `ws://127.0.0.1:8787/ws`
- `NEXT_PUBLIC_PLATFORM` — `desktop` | `web`
