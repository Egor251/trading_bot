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

### Known intermittent: test_historical_service_stubbed

`ERROR tests/test_historical_data.py::test_historical_service_stubbed` appeared in
**2 of 6 full-suite runs**, and never in isolation (7/7 clean) or alongside its
nearest neighbours (`test_event_loop_hazards`, `test_pipeline_is_alive`, 29/29
clean). It is an `ERROR`, not a `FAIL` -- the test itself asserts nothing
relevant to the failure, which points at setup/teardown rather than the
assertions.

Not fixed, and not worked around. The same reasoning as the Quik flake applies
with less information: an unreproducible failure that is worked around becomes a
failure that is hidden. Recorded here so the next person does not spend an hour
deciding whether it is new.

Worth noting what it is *not*: it appeared alongside changes to
`historical_data_service` and after they were reverted, so it does not correlate
with any specific edit made in this line of work.

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

Measured on real Bitget data, `BTC/USDT` 1h, 1000 bars, floor 0.25:
**19 of 20 strategies traded, 10 of those 19 profitable.**

```
dual_thrust             11 trades  243 rejected   +338.58
mean_reversion_example  10 trades   23 rejected   +219.68
momentum_roc            11 trades   45 rejected   +218.44
vwap_reversion          11 trades   10 rejected   +217.88
williams_r_reversion    11 trades   34 rejected   +208.48
cci_reversion           10 trades   28 rejected   +198.20
bollinger_reversion     10 trades   29 rejected   +197.47
supertrend_follow       10 trades   20 rejected   +196.91
rsi_reversion            8 trades    6 rejected   +188.21
macd_trend               3 trades   86 rejected    +34.71
ema_cross                1 trade    39 rejected     -0.22
ma_cross_example         6 trades  153 rejected   -114.62
turtle_breakout         10 trades   37 rejected   -209.53
adx_di_trend            11 trades   50 rejected   -220.83
heikin_ashi_trend        7 trades  374 rejected   -220.91
parabolic_sar_follow    11 trades   62 rejected   -231.43
volume_impulse_example  10 trades   39 rejected   -232.98
stochastic_reversion    11 trades   16 rejected   -285.44
keltner_breakout        11 trades  107 rejected   -310.42
```

`volatility_breakout_example` traded nothing (every signal below
`strength_floor`).

**This is a smoke signal, not a research verdict.** One 1000-bar window, no
walk-forward, no untouched holdout, no parameter search — the research gate
requires all of those before anything may trade live. Do not read the ordering
above as a ranking.

### A shorter window is not a cheaper version of the verdict

The same sweep was run twice on the same data — 400 bars and 1000 bars. The
rankings do not agree:

| | 400 bars | 1000 bars |
|---|---|---|
| top 5 by 400-bar result | **+621.54** | **−337.58** |
| bottom 5 by 400-bar result | **−1115.84** | **+304.12** |
| Spearman ρ(400, 1000) | −0.337 | exact permutation **p = 0.159** |
| same sign in both | 7 of 19 | 12 of 19 sign flips |

The point is not that short windows are anti-predictive — p = 0.159 cannot
support that claim, and this is one asset with overlapping windows. The point is
that **a 400-bar ranking carries no demonstrable selection information about the
1000-bar ranking.** Selecting on it would have bought the −337.58 group instead
of the +304.12 group, and that is arithmetic rather than inference.

An earlier revision of this runbook carried a different top five. **Three of its
five members are negative in the table above.** That table has been replaced
rather than softened, and its figures are deliberately not repeated here — a
superseded measurement left in the documentation reads as evidence long after it
stopped being any. `tests/test_sweep_window_agreement.py` fails if the current
table and the documented one diverge, and also fails if the old numbers reappear.

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

## Why did it trade?

The trade journal (`data/ops/trades.jsonl`) records **fills**: symbol, side,
price, fee. That is what an accountant needs and it is exactly what is missing
when a strategy starts bleeding. It cannot answer "what was the input", and it
never sees the decisions that did not fill — including every `observe_only`
signal, which is the entire evidence base for a strategy on probation.

Meanwhile `StrategyDecision.metrics` computes the features every bar, carries
them one hop in `params_snapshot`, and is then discarded by the ensemble
service, which keeps only quantity/price/class_code/bar_ts. The features were
computed, briefly existed, and vanished.

`engine/decision_store.py` keeps them. Enabled in config:

```yaml
trading:
  ensemble:
    decision_store_path: data/ops/decisions.jsonl
    decision_hold_sample_rate: 0.05
```

Non-HOLD decisions are recorded in full. HOLDs are sampled — recording every bar
of every symbol is a lot of JSONL nobody would afford to keep — but `recorded`
and `sampled_away` are counted separately in `get_status`, because a base rate
computed from recorded rows alone overstates activity.

`observe_only` signals are recorded *before* the observe early-return. They never
become trades, so they never reach the trade journal; dropping them here would
have made probation unmeasurable.

### Drift against the validated envelope

Keeping decisions is only worth something if something reads them. Freeze the
envelope at the point validation was done — after a walk-forward pass, after a
research sweep:

```python
svc.freeze_decision_baseline("supertrend")
svc.check_feature_drift("supertrend")
```

`check_feature_drift` reports features whose live range has left the range the
strategy was measured on. That is terrain the validation never covered, and no
P&L chart shows it — the trade journal only shows the trades that resulted.

A strategy with **no baseline is not being checked**, and the result says
`checked: false, reason: no_baseline`. That silence must not be read as "no
drift". In `get_status`, `feature_drift` maps each baselined strategy to
`true`/`false` and every unbaselined one to `null`, so "not checked" and
"checked and clean" are distinguishable at a glance.

Retention is an in-memory ring (`max_rows`, default 200k) plus append-only
JSONL. `dropped_after_cap` is reported in `stats()` rather than silently
discarding rows.

## Protective stops are reduce-only at the venue

`place_native_stop` set `raw={"reduce_only": True}` on the broker order, and that
was the whole of it. `CcxtExecutionAdapter.place()` reads `clientOrderId`,
`stopPrice`, `timeInForce` and `postOnly` — and never looked at `raw` for a
reduce-only flag, so **nothing reached the exchange**. Every native stop was an
ordinary order that may open or add to the opposite side.

`reduce_only` is now a real field on `BrokerOrder` and is sent as `reduceOnly`.
The consequence being prevented: if the book is flat, or the protective slice
already closed, when the stop triggers it takes a position in exactly the
direction the stop was written to avoid.

### When the venue cannot honour it

`BrokerCapabilities.reduce_only` defaults to `True`, so the flag is sent by
default. Set it `False` for a venue that does not honour `reduceOnly`, and:

- the order is **still placed** — losing protection entirely is strictly worse
  than the reversal risk;
- the slice is recorded in `runtime._native_stop_weak_spots`;
- the protection audit raises `weak_protective_stop` at **WARNING**, not
  CRITICAL — there *is* a stop, so the loss is still bounded; what is missing is
  the guarantee it cannot reverse the book;
- `audit_position_protection()` reports `coverage` and `full_coverage`
  separately. Both are true, and the gap between them is the size of the
  reversal risk.

A venue that silently ignores `reduceOnly` is worse than one that rejects it:
we would believe the guarantee holds while it does not.

### Why the old test missed it

`test_protection_durability.py` asserted
`str(order.raw.get("reduce_only")) == "True"` against a fake `_RecordingPort`.
That port sees our `BrokerOrder` and nothing else — it never sees venue params.
The test verified a dictionary key in our own process and would have passed with
the flag deleted outright. It now asserts the real field, and
`tests/test_reduce_only_transmission.py` pins the transmission at the boundary,
on what `create_order` was actually called with.

### Config, and where the knobs actually bind

The store path may be set in **either** place, matching how `corr_cap` is read:

```yaml
trading:
  ensemble:
    decision_store_path: data/ops/decisions.jsonl   # <- read from here
services:
  ensemble_service:
    decision_store_path: data/ops/decisions.jsonl   # <- or here
```

A **relative** path is anchored at the data directory, not the process CWD.
Research tools redirect `TRADING_DATA_DIR` to a scratch directory; a CWD-relative
`data/ops/decisions.jsonl` would let a phantom sweep write into the live trading
data, which is the contamination that redirect exists to prevent. Absolute paths
are left alone.

Note that `tools/verify_execution_pipeline.py` deliberately overwrites
`_ensemble_cfg` with a bare lineup, so it will not pick these up — it is a
minimal pipeline probe, not a config test. `test_decision_store.py` reads the
real `config/trading_bot.yaml` for exactly that reason: a test with a
hand-built config would never notice the knobs sitting in the wrong section.

## Measured: no strategy here beats random timing

Measured 2026-10-04, BTC/USDT and ETH/USDT, 20 strategies, 4 disjoint windows
per slice, 20 random-timing nulls per window.

```bash
python -m tools.window_stability 1h BTC/USDT 1000 4 20
python -m tools.window_stability 1h ETH/USDT 1000 4 20
python -m tools.window_stability 1d BTC/USDT 1000 4 20
```

### Why random timing, and not a coin flip

Sign counts answer "is this plus/minus sequence unusual", which is not the
question. The question is whether a strategy beats random timing on the same
bars — and that comparison is only fair if the baseline pays what the strategy
pays. `strategies/random_baseline` is therefore a *strategy*: seeded, subscribed
to the same candles, evaluated by the same `run_grid`, so comparability is
guaranteed by construction rather than by matching two implementations by hand.

Windows are compared against the null **of their own window**. Windows on one
asset are not interchangeable, and a pooled null would flatter anything in a
window that simply went up.

### Result

| slice | windows at or above p90 of own null | expected under noise |
|---|---|---|
| BTC/USDT 1h | 9 / 80 | 8 |
| ETH/USDT 1h | 5 / 80 | 8 |
| BTC/USDT 1d | 10 / 80 | 8 |

Strongest strategies on BTC (1h) and ETH (1h): **one in common**,
`vwap_reversion`. Expected overlap of two independent rankings: 1.40. Observed: 1.

Against random *hold* rather than random timing, on 2.7 years of daily bars:
**42 of 80 windows below the null median** (52%). A coin flip gives 40.

**No strategy here is distinguishable from chance.** Every one stays
`research_only`, and the reason is now a measurement rather than an inference.

### The bars are not the limiting factor

1000 bars is 41 days at 1h and 2.7 years at 1d. The bar budget is fixed; the
time span is not. The daily run also shows the null's median is *systematically
positive* (+114.80, +179.88, +2.77, −178.84) while hourly medians sit near zero:
long horizons contain a strong uptrend that any holder captures and a trader pays
costs to miss. Absolute P&L over years must not be read as an edge for that
reason.

### What would change this

More slices, each with its own null — not parameter tuning, which would
reproduce the best-of-N illusion this measurement exists to prevent. A strategy
that shows up at p90 on one asset, one timeframe and one window has shown
nothing: 80 windows at a p90 threshold expect 8 false positives, and 9, 5 and 10
were observed.

### The instrument has been wrong nine times

Every guard in `window_stability` was written because the measurement was
already broken when it was added, and every guard except the first was itself
too narrow. Listed so the next person does not trust it blindly:

1. short fetch → WARN
2. identical results across windows → ABORT (only in windows that traded)
3. zero trades → WARN/ABORT (**on the null as well as the strategies**)
4. ten guards in all, including `run_grid` reporting the timeframe it used

Two of the nine faults were found by an instrument reporting a *confident*
answer: `bollinger_reversion` at 4/4 and `cci_reversion` at 4/4 were two
strategies reading one ledger. And the null once came back as a clean
distribution of 0.00 that 33 of 80 windows "beat".

### The threshold was calibrated, not assumed

Every verdict above compares an observed count against a nominal 10%. That
expectation assumes the windows are independent trials. They are not — they are
adjacent stretches of one market.

```bash
python -m tools.null_calibration
```

Synthetic series, sampled with three correlation structures:

| structure | false positives at p90 | nominal |
|---|---|---|
| `independent` — windows share nothing | **12.3%** | 10% |
| `market` — one shared factor per window | **19.5%** | 10% |
| `serial` — and adjacent windows correlated | **22.0%** | 10% |

`market` is not a worst case invented for effect: it is what was measured on
BTC, where window three made every strategy look strong simultaneously. `serial`
adds the regime carry-over between windows, which real windows have and
independent trials do not.

**The observed rates were 11.3%, 6.3%, 12.5% and 14.3%** — every one at or below
the calibrated ~20% for the correlated case. So the negatives are stronger than
they looked: the noise floor for this method is higher than the nominal figure,
and the strategies did not clear even that.

This is the one thing synthetic prices are genuinely good for. A random walk has
no edge by construction, so a strategy that clears p90 against it is a false
positive *by definition* — the draws cannot find an edge, they can only measure
whether the measuring works.

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
