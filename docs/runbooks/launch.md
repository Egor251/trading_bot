# Launch

All commands from the repository root (`trading_bot/`).

## Engine only

```bash
pip install -r requirements.txt
# or: pip install -e ".[dev]"

python main.py --smoke
python main.py --repl
python main.py --headless
# equivalent:
python host/main.py --headless
```

## Engine + Operator UI

```bash
python run_operator.py              # Tauri desktop (primary)
python run_operator.py --web        # FastAPI BFF :8787 + Next.js :3000
python run_operator.py --desktop    # explicit Tauri
# or: set OPERATOR_UI=desktop|web
```

Two processes manually:

```bash
python host/main.py --headless
python -m ui --web                  # BFF only
# Desktop:
cd ui/desktop && npm install && npm run dev
# Web browser:
cd ui/web && npm install && npm run dev
```

## Health check (container / ops)

```bash
python scripts/healthcheck.py --state-file data/health.json
# or:
python tools/healthcheck.py --pid 1 --process-pattern host/main.py
```

## Tests

```bash
python -m pytest -q
```

The suite is isolated from the live `data/` tree via `TRADING_DATA_DIR`; an
autouse fixture in `tests/conftest.py` fails any test that writes there.

### Known flaky: test_quik_fake_path_e2e

Fails intermittently (~5% observed, most often inside a full-suite run; 20/20
clean in isolation and 3/3 clean full-suite runs at the time of writing). It
does not use `replay`, `phantom` or `historical_data_service`, so the research
and execution changes in this repo are not in its path.

Its waits are already deadline-based rather than fixed sleeps, and the fill
assertion carries the received `fills` so a failure is diagnosable. The cause
has not been isolated; inventing a fix for an unreproducible failure risks
making it worse. If it appears, the assertion payload is the starting point.

## Research gate

`trading.live_readiness` is **enforced** at Consilium start-up. Any member of
`trading.ensemble.strategies` that does not reach `live_ok` is demoted to the
observe roster, logged at CRITICAL and published as
`ensemble.reject{reason: "research_gate_demoted"}` with the specific blockers.

`live_ok` requires holdout clearance (via `phantom_walk_forward` with
`holdout_bars > 0`), `robust_sign_match`, a confidence interval excluding zero,
and `min_oos_trades` out-of-sample trades — with `max_age_days` expiry.

Read the whole gate state with `python tools/gate_status.py` and one strategy's
evidence with `python tools/clearance_table.py <strategy>`.

### Clearance depends on how much history the venue would serve

The venue caps a single klines request (Bitget: 300 daily bars), and the adapter
used to stop there. Every clearance entry written before that was fixed was
computed on a **truncated window** and said so nowhere.

`dual_thrust` regenerated on the full 1000-bar window versus the old 300-bar one:

```
entry                holdout_bars  mean_oos_pnl   ci                robust
BTCUSDT  (old, 249)          249          +5.13  [-5.68, +27.41]     True
BTCUSDT  (new, 459)          459         -57.29  [-131.04, -0.64]   False
ETHUSDT  (old, 249)          249         +87.93  [+9.77, +59.72]     True
ETHUSDT  (new, 459)          459         +36.69  [+6.00, +67.38]    False
```

The truncated window made the strategy look robust on all three tickers. On
2.7 years of data it is not robust on any of them, and BTC's out-of-sample PnL
is significantly **negative**. `dual_thrust` consequently fell from `observe_ok`
to `research_only`, and **all 20 strategies are now `research_only` — nothing
is cleared to trade at all.**

Do not widen a gate threshold to admit a strategy whose own evidence is
adverse. `robust_sign_match` and a negative-sign CI fail first; there is no
threshold relaxation that turns this evidence positive.

The gate used to be advisory only: nothing on the decision path called it, so a
YAML line was the sole control. `enforce: false` restores that behaviour.

## Live arming requires a tradeable lineup

`SET_TRADING` to live is refused with `live_lineup_not_tradeable` unless every
listed strategy passes all nine preflight gates **and** is cleared to trade.
The nine gates checked plumbing; none of them asked whether anything was
research-cleared, so arming silently succeeded with an empty lineup. The refusal
lists the per-strategy reasons.

## Reading a phantom result

`run_grid` returns **one row per variant**, and that is now the only shape it
returns. A run also reports why signals did not become trades:

| field | meaning |
| --- | --- |
| `trades`, `fills` | fills recorded in the shadow ledger |
| `rejected`, `reject_reasons` | signals dropped, by normalised reason |
| `unfilled_signals` | signals raised on the final bar, with no next bar to fill |
| `settle`, `unsettled_bars`, `dropped_events` | bus delivery report for the replay |

**`trades=0` is not a verdict on the strategy.** Read `reject_reasons` first. A
run where every signal fell below `strength_below_floor` looks identical to a
strategy that never traded unless the rejects are reported. They are now.

Notable reasons: `strength_below_floor` (signal weaker than
`phantom.strength_floor`), `exit_already_pending` (an exit while one is already
open), `order_rate` (throttled), `net_flip_forbidden` (long/short model), and
`qty_below_lot_size`.

Measured on real Bitget data, `BTC/USDT` 1h, 1000 bars, floor 0.25, one strategy
per run: **19 of 20 strategies traded, 5 of those 19 profitable.**

```
parabolic_sar_follow    11 trades   62 rejected   +249.64
heikin_ashi_trend       11 trades  377 rejected   +239.04
dual_thrust             11 trades  241 rejected   +218.32
supertrend_follow       10 trades   19 rejected   +164.63
adx_di_trend             9 trades   42 rejected   +110.96
macd_trend               6 trades   86 rejected    -88.40
rsi_reversion            7 trades    6 rejected   -115.45
momentum_roc            10 trades   44 rejected   -155.74
vwap_reversion          10 trades   11 rejected   -168.70
cci_reversion           10 trades   29 rejected   -165.63
volume_impulse_example  10 trades   37 rejected   -199.02
williams_r_reversion    11 trades   34 rejected   -198.80
mean_reversion_example  10 trades   23 rejected   -194.14
turtle_breakout         10 trades   36 rejected   -208.16
keltner_breakout        11 trades  105 rejected   -276.16
bollinger_reversion     11 trades   28 rejected   -152.11
stochastic_reversion    11 trades   16 rejected   -285.44
```

`volatility_breakout_example` traded nothing (every signal below
`strength_floor`); `ema_cross` managed a single trade.

**This is a smoke signal, not a research verdict.** One 1000-bar window, no
walk-forward, no untouched holdout, no parameter search — the research gate
requires all of those before anything may trade live. Do not read the ordering
above as a ranking.

Reproduce with `python tools/strategy_sweep.py 1h BTC/USDT 1000` (~110 s for all
20, nearly all of it the per-bar settle below).

## Replay fidelity

**The replay must run on the timeframe the research run asked for.** `run_grid`
omitted `timeframe=` from `hist.replay(...)`, so every replay silently fell back
to the service default of 1m while the result was still labelled 1h. Measured on
`BTC/USDT` "1h": the replay published 60-second bars from the tail of the
requested window, and momentum ROC collapsed from ±0.036..0.059 to
±0.008..0.005 — 89 threshold crossings became 0, so `momentum_roc` and
`vwap_reversion` reported no signals at all. `replay` now verifies the served
series against an explicitly requested timeframe and raises `timeframe_mismatch`
rather than mislabelling; `timeframe_requested` / `timeframe_served` are in every
replay result.

### Delivery ordering

`EventBus.publish` only enqueues; a single worker dispatches later. Replay
therefore **settles each bar before publishing the next**, so a decision on bar
N is dispatched, becomes a signal, and only then does bar N+1 arrive to fill it.
Cost is ~1–2 bus round-trips per bar (~33 s per 1000-bar replay) — that is the
price of exact fill ordering.

Without it, every `next_open` fill of the run is discarded and the result reads
`trades=0` while the strategies are signalling normally. `replay_settle_per_bar:
false` restores the fast path; it is for smoke runs only, because it makes fill
ordering untrue. `replay` also reports `unsettled_bars` and `dropped_events` so a
history that could not be delivered in full is visible rather than silent.

History depth is a separate trap: a venue may serve far less than the requested
limit (Bitget returns 300 daily bars for `limit=5000`) and may ignore `since`
entirely. The adapter walks backwards with `endTime`, falls back to `since` when
that is ignored, and stops only when a page yields no new timestamps — a *short*
page is not exhaustion. Verified: 1000 daily bars spanning 2.7 years.

## Journal maintenance

The event journal is unbounded. `tools/retention.py` reports first and deletes
only on an explicit `--apply`:

```bash
python -m tools.retention --status          # rows, size, distribution
python -m tools.retention                   # dry-run plan
python -m tools.retention --index           # add the pruning index
python -m tools.retention --apply --vacuum  # prune, then reclaim disk
```

Retention is keyed by event type, not by `data_class` (the latter is
`semantic_stream` / `finding` / `audit` / `state` and carries no retention signal).
The trading record has a 365-day floor — `execution.result` and `risk.reject` are
never pruned inside a year — and `system.halt`, `ops.alert` and
`reconciliation.*` are never pruned at any age, because the incident runbooks
treat the journal as the authority. Free pages are only returned to the
filesystem by `VACUUM`.

## Operator session token

The BFF authenticates `/api/*` and the WebSocket with `X-Operator-Token`. It
resolves from `OPERATOR_API_TOKEN`, else from `data/ui/operator_token` (mode
0600), else it is generated at first boot. A non-loopback `--host` requires
`--allow-lan`.

## Control plane

Install `TRADING_CONTROL_HMAC_SECRET` (>=32 bytes, see `.env.example`). Without
it the engine accepts **only** `HALT` and logs a CRITICAL at boot. With
`allow_unauthenticated_local: true` it additionally accepts `CONFIG` — never
`RESUME` or `LIVE`.

Restore pre-cleanup v1 tree: `git checkout legacy-v1`.
