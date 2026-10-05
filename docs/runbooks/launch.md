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

### The market factor is stripped by exposure, not by median

`WINDOW_RESIDUAL=1` repeats the comparison after removing what the market paid
everyone. A market pays whoever holds, in proportion to how much they hold, so
what belongs to one strategy is its share of the window's total exposure times the
window's total P&L:

```
residual = net - (exposure_i / sum_exposure) * sum_net
```

```bash
$env:WINDOW_RESIDUAL="1"
python -m tools.window_stability 1d BTC/USDT 3000 4 20
```

**The denominator used to be wrong.** Subtracting the median across strategies
assumed exposure 1 for every strategy. `volatility_breakout_example` places zero
trades here, so it has exposure 0 and net 0, and median subtraction handed it
`0 − median`: a market position it never held. Measured on this run, that gave a
strategy with a raw P&L of **0.00 in all four windows** residuals of **−934.94,
+901.76, −5319.45, +188.87** — non-zero everywhere, sign following the peer median.
It now reads **exactly 0.00 (p45–p50) in every window**, which is the only honest
number for a strategy that never placed an order.

Severity stated precisely, because an earlier version of this note claimed it read
p95 in every window and that is not what the numbers do: half those residuals are
negative. The bug never promoted an inactive strategy to a winner — it gave a
strategy with no position a market exposure in every window, up to 5319 of it.

`trades` is the exposure proxy: the same count the zero-trade guard already treats
as proof that no P&L can exist, so the denominator and the invariant cannot drift
apart. It assumes P&L scales with trade count rather than with held notional or
time in position, and a strategy that trades small and often is under-weighted by
it. The result row carries no notional. Known gap, stated rather than replaced by
another wrong denominator.

#### Measured: removing the factor does not clear the field

| BTC/USDT 1d, 3000 bars, 4 windows | windows at or above p90 |
|---|---|
| raw P&L | **12 / 84** |
| after removing the market factor (time-weighted) | **11 / 84** |
| after removing the market factor (notional-weighted) | **14 / 84** |

The raw count reproduces the 12/84 already recorded above. All three counts sit at
or below the calibrated **16–18 of 84** noise floor for this window structure, so
**nothing here clears the noise** and all 21 strategies remain `research_only`.

Note the middle column is the honest one: swapping the denominator moved the count
by three windows. A filter whose own settings change the answer by 11% of the
sample is not a filter to draw conclusions from. The denominator below was chosen
on a correlation test registered before the run, not on which count looked better.

### What P&L is actually built out of

Three explanations for why no strategy separates were tested. Two were refuted by
measurement, one survived, and the survivor only works half way.

**Turnover mismatch** — a null trading more often than the strategies would inflate
the p90 bar it defines. Refuted: strategies place 6.05–8.05 orders per window and
the null 5.95–6.50, a ratio of 0.83–1.07x. `forbid_net_flip` runs research
long/flat, which collapses the null's turnover to match.

**Cost drag** — the bill for making decisions could be crushing everything. Refuted:
2.51–5.32 per window against P&Ls of ±5000, about 0.05%.

**Exposure** — net P&L is the market's move multiplied by how long and how big the
book was, so comparing gross P&L compares exposure and calls the difference skill.
This one is real, and it took two attempts to pin down:

| proxy | r(exposure, P&L) in w3 | verdict |
|---|---|---|
| `trades` | not a proxy at all — a strategy holding two years and one holding two weeks both place a handful of orders | rejected |
| `bars_in_position` | **0.103** | rejected |
| notional per bar | **0.673** | factor identified |

Across all four windows the notional correlation is 0.210, **−0.722**, 0.673,
0.278. The two large values are w2 (total P&L −38 409) and w3 (+49 054); the two
calm windows are w1 (+3 406) and w4 (−4 115). A factor that shows up when the
market moves and disappears when it doesn't is a market factor.

Both metrics are new and neither existed anywhere in the stack before this:
`exposure_from_fills` (bars held, merging overlapping stretches so a scale-in
counts once) and `notional_exposure_from_fills` (the integral of position value
over time, valued at each stretch's entry fill because the ledger carries no
per-bar price series). Both are additive: no existing metric changes value.

### The denoiser still does not work, and the criterion that said so was registered first

After removing `notional × (total net / total notional)`, the residual must stop
tracking exposure. Registered before the run: **|r(notional, residual)| < 0.2**.

| window | before | after | |
|---|---|---|---|
| w1 | 0.210 | 0.165 | clean |
| w2 | −0.722 | −0.067 | clean |
| w3 | 0.673 | **0.435** | still tracking exposure |
| w4 | 0.278 | **0.504** | worse than before |

**The criterion failed.** The linear exposure-proportional scalar removes the
dependence in w2 and most of it in w3, leaves a third of it in w3, and *adds* it
in w4 — a window where there was no factor to remove in the first place. One
scalar times one exposure cannot represent this structure, and subtracting more of
them would only make w4 worse.

So the honest position: the factor is real, it is measurable, it is now instrumented
end to end, and the denoiser built on it is still an approximation that has failed
its own test. The raw count is the one to quote.

`time_series_momentum` stays negative after the factor is removed (w1 −3933.81,
p5) and clears p90 in no window. Its parameters stay at the article's values on
purpose: tuning them until a plus appears is best-of-N selection, which is the
illusion this whole measurement exists to catch.

### The binding constraint is arithmetic, not signal

Four windows and twenty strategies cannot express a claim at all. The best p-value
any strategy can reach by being positive in every window is `P(4/4) = 0.0625`,
against a Bonferroni threshold of `0.05/20 = 0.0025`. Measured sign consistency
today: `ma_cross_example` and `mean_reversion_example` at 4/4, expected 1.25 such
out of 20 by chance, observed 2. Noise.

| windows | best achievable p | reachable |
|---|---|---|
| 4 | 0.0625 | no |
| 8 | 0.0039 | no |
| **10** | **0.00098** | **yes** |
| 16 | 0.00002 | yes |

**At least 10 windows, and 16 to be comfortable, before any result can be
significant — regardless of how good the strategies are.** Adding more strategies
makes this worse, not better: it divides the threshold further.

The other missing measurement was **power**: `tools/null_calibration.py` measured the
false-positive rate and nothing had ever checked whether the instrument can see a
*real* edge. It has now, and the answer reverses the standing worry.

### Power: the instrument works

An edge is injected as predictable next-bar returns. `phi` is the share of each
bar's move knowable in advance from the previous bar — the fraction of return
autocorrelation that would have to exist. The conversion is exact
(`E|z| = sqrt(2/pi)` against per-bar noise `sigma`), so the axis means something
outside this file.

```bash
python -m tools.null_calibration
```

| phi | snr | windows clearing p90 | vs noise |
|---|---|---|---|
| 0.00 | 0.00 | 19.6% | — |
| 0.05 | 0.32 | 26.2% | +6.5 pp |
| 0.10 | 0.64 | 33.9% | +14.3 pp |
| 0.20 | 1.28 | 51.6% | +32.0 pp |
| 0.30 | 1.92 | 68.4% | +48.7 pp |
| 0.50 | 3.19 | 91.1% | +71.4 pp |
| 0.80 | 5.11 | 99.6% | +80.0 pp |

`phi = 0` lands on 19.6% against the 19.5% the false-positive calibration already
reported, which is the harness checking itself: injecting nothing must not move the
answer, and it does not.

**So the negatives are informative after all.** An edge of `phi = 0.05` — five
percent of each bar's move predictable — already lifts detection 6.5 pp above the
noise floor. The observed counts (11–14 of 84) sit at or below that floor, so none
of these 21 strategies carries a predictable component even at that threshold. The
worry that "no strategy cleared p90" was equally consistent with "the instrument is
too blunt to see" is now settled in the other direction.

The honest limit is resolution, not blindness. Going from the noise floor to 50%
takes `phi ≈ 0.20` — a fifth of every bar's move predictable, which is not a
market, it is a broken random number generator. So this method cleanly separates
"nothing" from "a great deal", and has little resolution in between.

**Per-window detection does not improve with more windows** — 70.5% / 68.5% /
68.7% / 68.5% at 4 / 8 / 16 / 32 windows. It has to be flat: p90 is judged inside
one window against one null, so the window count is not in that calculation. What
more windows buy is the ability to make a claim at all, which is the reachability
table above — exact, `best p = 2^-windows` against `0.05/S`, and unchanged by any
improvement in data quality. An earlier draft of this tool claimed power rose with
windows; it does not, and the claim was a family-level statistic under a per-window
heading.

### The ceiling: how predictable is the market itself?

Every negative result has a second explanation that had never been excluded — the
*market* may be unpredictable, in which case no strategy could have found anything
and the measurement was never at fault. That is measurable without running a single
strategy, because the power axis is an autocorrelation and correlations can be
estimated on the price series directly.

```bash
python -m tools.edge_ceiling 1d BTC/USDT,ETH/USDT 3000 4
```

| series | AR(1) phi | se | t | VR(2) | VR(5) | VR(10) |
|---|---|---|---|---|---|---|
| BTC/USDT 1d | **−0.0677** | 0.0299 | −2.26 | 0.933 | 0.965 | 1.005 |
| ETH/USDT 1d | **−0.0721** | 0.0276 | −2.61 | 0.928 | 0.969 | 1.008 |

Newey-West standard errors, because an i.i.d. one errs in the convenient direction
on heteroskedastic returns.

Two findings, and the second is the important one.

**There is no positive predictability of any size.** Both series are *below* the
detection floor of 0.05 in magnitude — so nothing this method could notice exists.
At longer lags both variance ratios sit on 1.000 and 1.008: consistent with a random
walk. There is no trend to ride at the daily scale of these assets.

**The one dependency that is there has the wrong sign.** Lag-1 autocorrelation is
significantly *negative*, −0.07 on both, t = −2.26 and −2.61. Research runs
long/flat (`forbid_net_flip`), so this lineup can only be long; a long entry after
an up day is buying into a statistically adverse next bar. A trend-following lineup
is not failing to find an edge of detectable size — it is on the adverse side of
the only dependency present. That is the answer to *why it doesn't work here*, and
it is a property of the price series, not of the strategy list.

Caveat, stated because it is the obvious objection: a negative lag-1 autocorrelation
at daily scale is frequently microstructure rather than something tradable. Nothing
here claims it is harvestable — only that it is the wrong sign to be exploited by a
long-only lineup, and that no positive autocorrelation of any size exists for it to
exploit.

Per-window `phi` is 0.024 to 0.132 across all eight windows and **none of them is
significant**, because a 748-bar window cannot resolve less than 0.072
(`1.96/sqrt(n)`). Only the pooled figure is strong enough to read, which is exactly
what that arithmetic is for.

### Ten windows: the first design that can express a claim

```bash
python -m tools.window_stability 1d BTC/USDT 3300 10 20
```

The daily depth is the binding constraint and it is hard: requesting 12 000 bars
returns **3 336** — about nine years, and no further. Ten windows therefore means
**329 bars each**, just under a year.

| | windows at or above p90 | rate |
|---|---|---|
| raw P&L | 28 / 210 | 13.3% |
| after removing the market factor | 30 / 210 | 14.3% |

Both **below** the calibrated ~19.5% noise floor for this window structure.

The registered test was whether any strategy is positive in *all ten* windows, since
`P(10/10) = 0.00098` is the first outcome that clears Bonferroni at 21 strategies
(`0.05/21 = 0.00238`). Expected under chance: **0.020 strategies**. Observed:
**zero**.

The closest miss is `volume_impulse_example` at 9/10 positive, raw and after
demeaning alike. It does not survive its own test, and the reason is worth keeping:
`0.5^9 = 0.00195` looks like it clears 0.0025 until you count two things — that
`0.5^k` is `P(X=k)` and not the one-sided tail `P(X>=k) = 0.01074`, and that twenty
strategies were measured. The corrected figure is **0.215**. One strategy out of
twenty reaching 9/10 is expected roughly one time in five by chance alone.

Two further things did not survive contact with more windows. The market factor was
identified in **1 of 10** windows (r = 0.798 in w4), against 2 of 4 with 749-bar
windows — fewer bars per window means less signal to estimate a factor from. And the
denoiser's criterion was met in only **4 of 10** windows, failing badly in w4
(0.719), w5 (−0.629), w6 (0.549) and w1 (−0.566). Short windows make the linear
exposure model worse, not better.

So: the design now *can* state a claim, and the claim it states is that none of these
twenty strategies has an edge. That is a stronger statement than any previous run in
this project, because it is the first one where the design was capable of expressing
a negative.

### The negative dependency is not tradable

One lead survived the ceiling measurement: the lag-1 dependence is *negative*, so
the signal is a short. Research runs long/flat, so it is structurally unreachable —
which made it worth one direct test rather than an argument.

The rule is fully determined by the measured coefficient and nothing else: short
after an up bar, long after a down bar, flat otherwise. No parameter, no threshold,
nothing to tune. Signal from the close of bar *t*, held from the open of *t+1* —
filling on the signal bar would be lookahead and would just report the coefficient
back to itself.

| | BTC/USDT | ETH/USDT |
|---|---|---|
| phi | −0.0677 (t = −2.26) | −0.0721 (t = −2.61) |
| daily sigma | 3.26% | 4.36% |
| **breakeven cost** | **35.3 bps per fill** | **50.2 bps per fill** |
| in-sample gross | +220.7% | +362.2% |
| position changes | 3209 over 2999 bars | 3213 over 2999 bars |

**The in-sample gross is worth nothing.** The coefficient was fitted on the same
bars the rule was then run over. Carrying only the *sign* across a half-sample
exposes what is left:

| direction | train phi | gross | cost @10bp | net |
|---|---|---|---|---|
| BTC first → second | −0.0807 | **−22.3%** | 157.1% | −179.4% |
| BTC second → first | −0.0344 | +243.0% | 163.9% | +79.1% |
| ETH first → second | −0.0907 | +87.0% | 159.5% | −72.5% |
| ETH second → first | −0.0259 | +275.2% | 161.9% | +113.3% |

BTC loses money at *zero cost* in one direction. The gross swings from −22% to +243%
on nothing but the choice of which half the sign came from — the signature of a
dependence that is not harvestable.

**The decisive number is the breakeven cost: 35–50 bps per fill**, for a rule that
changes position 1.07 times per bar, so it pays two fills per bar. Spot taker costs
on BTC/USDT run roughly 4–10 bps round trip before slippage, funding or error. The
rule would need costs four to ten times better than the market offers.

So the dependency is real in the statistical sense — |t| > 2 and stable across
Newey-West lags from 1 to 30, which is not what a fragile estimate looks like — and
not tradable in the arithmetic sense. Both statements are needed, and the first alone
would have been the more interesting-sounding one.

**Closing position, on measurements rather than inference:** on daily BTC and ETH, at
this bar budget, there is no positive predictability of any size to find, and the one
dependency that is present cannot pay for its own turnover. There is nothing here to
win, and no configuration of strategies will change that, because the constraint is
in the price series.

### Another class of assets and horizons — registered, and refuted

The one lead left was the asset class itself. Positive return autocorrelation is not
expected anywhere on daily crypto, so the a priori case for looking elsewhere is the
managed-futures result: trend pays at horizons of weeks to months, and it pays across
asset classes rather than in one. That predicts two things a registered grid can fail:
**positive phi at weekly horizons, and positive phi outside crypto.**

Registered before the sweep, so a positive reading could not be turned into a
hypothesis afterwards — 6 series x 2 horizons = 12 tests, and three gates:

1. `phi > 0` with `t > 2.70` (one-sided Bonferroni for 12 tests)
2. the sign must carry out of sample, both directions
3. breakeven under 10 bps per fill and under 0.5 flips per bar

```bash
$env:EDGE_SWEEP="1"
python -m tools.edge_ceiling 1d BTC/USDT,ETH/USDT 3000 4
```

| series | 1d phi | 1w phi | class |
|---|---|---|---|
| BTC/USDT | −0.0677 (t −2.26) | +0.0033 (t +0.07) | crypto |
| ETH/USDT | −0.0721 (t −2.61) | +0.0222 (t +0.49) | crypto |
| ^GSPC | **−0.1261** (t −1.91) | −0.0485 (t −0.74) | US equity index |
| GC=F | −0.0289 (t −1.28) | **−0.1219** (t −2.27) | gold future |
| EURUSD=X | +0.0075 (t +0.34) | −0.0840 (t −1.65) | FX |
| AAPL | −0.0478 (t −1.20) | −0.0525 (t −1.11) | single equity |

**Zero of twelve passed gate 1.** The hypothesis is refuted, and refuted in a specific
way: **nine of twelve are negative, and weekly is generally *more* negative than
daily.** Gold at −0.122 weekly and the S&P at −0.049 weekly are the opposite of what
"trend pays at longer horizons" predicts. The three positive readings are +0.003,
+0.008 and +0.022 — all far inside noise, which is what an expected-value-near-zero
process looks like.

Notably, the sweep did **not** produce the false positive it was designed to catch. With
12 independent tests and a t-bar of 2.70, roughly 0.04 passing by chance was the
prediction; zero passed. That is evidence the correction is doing its job and that the
absence of positive autocorrelation in these series is real rather than merely unfound.

Two caveats on the instrument, both worth keeping:

- **Gate 3 can never pass for this rule family.** A rule that flips on the sign of the
  previous bar changes position about once per bar by construction, so its turnover is
  ~1 and it fails a "< 0.5 flips/bar" test regardless of whether the edge is large.
  Gate 1 is the gate that carries this result; 2 and 3 never bound.
- **A gap in the data path was found and fixed on the way.** `yfinance` was missing
  from the environment though declared in both `pyproject.toml` and `requirements.txt`.
  More seriously, `yahoo_adapter._interval_days("1d")` returned `1.0`, so a request for
  300 daily bars fetched 300 *calendar* days and returned about 206 — with no error.
  Every correlation on that series was quietly computed on two thirds of the intended
  sample. Fixed to `365/252` calendar days per trading day, with the intraday branches
  now asset-class aware (crypto 24h, equities 6.5h). This is the tenth instance of one
  shape in this project: a quantity recorded in one place and interpreted in another.

### Where that leaves the search

The principled search is finished. Widening the grid now — more symbols, more
timeframes — is precisely the multiple-comparisons trap the sweep above was built to
detect, and the tool says so itself when it prints `None`.

Anything further has to be a **new hypothesis with an economic reason stated before
the data is seen**, each with its own registration: a named risk premium (carry, value,
a defined momentum definition) on a named instrument at a named horizon, judged against
its own multiplicity. That is a different piece of work from "look harder", and the
difference is whether the reason for looking was written down first.

### Momentum at registered horizons — not refuted, structurally untestable

Two of the three candidates could not be run honestly at all. **Carry** is the forward
premium and the interest-rate differential; there is no forward curve or rate feed in
this project, and substituting a long-run drift measured on one sample is the fit this
tool exists to refuse. **Value** needs fundamentals — P/E, book value — and there are
none; a cheap-versus-growth ETF pair is measurable but tests a drift on a single
sample, which is a well-known way to find nothing and a famous way to fake something.

**Momentum** was run, and the outcome is not a verdict but a wall.

```bash
$env:EDGE_MOMENTUM="1"
python -m tools.edge_ceiling 1d BTC/USDT,ETH/USDT 3000 4
```

7 horizons registered: 1, 5, 10, 21, 63, 126, 252 bars. **Only 4 of them could be
measured at all.** The daily ceiling is 3 336 bars, so resampled observations are:

| h (bars) | 1 | 5 | 10 | 21 | 63 | 126 | 252 |
|---|---|---|---|---|---|---|---|
| observations | 2999 | 599 | 299 | 141 | **47** | **23** | **11** |
| resolvable phi | 0.036 | 0.080 | 0.113 | 0.165 | 0.286 | 0.409 | 0.592 |

The three long horizons — the ones where time-series momentum is supposed to live —
produced 47, 23 and 11 observations. Nothing can be concluded from 11 points, and the
registration of them was my error: I specified horizons the data budget cannot support.

Among the 24 tests that did run, **none passed gate 1**. The strongest reading was
`BTC/USDT` at h = 21: `phi = +0.1988, t = +2.89`, below the 3.10 bar for 42 tests,
and barely above what 141 observations can resolve.

**So momentum is neither confirmed nor refuted — it is untestable here**, and the
reason is arithmetic rather than disappointing:

| target | observations wanted | daily bars needed | years |
|---|---|---|---|
| h = 21 | 500 | 10 500 | 29 |
| h = 63 | 500 | 31 500 | 86 |

Crypto has no 86-year daily history. Daily-bar momentum at a meaningful sample size is
therefore not measurable in this asset class, at any budget the exchange will serve.
The only route to more observations is intraday data, which measures *intraday*
momentum — dominated by microstructure, and precisely where costs bite hardest, as the
one-bar rows show at −541% for BTC.

Gate 3 did become reachable at longer horizons, as intended: turnover falls from 1.07
position changes per bar at h = 1 to 0.82 at h = 21, and several long-horizon rules
did clear 10 bps per fill. That part of the design worked. It simply had nothing to
qualify.

### The budget was a Binance limit, not a property of the market

Before accepting "untestable", the ceiling itself was checked — and it was the wrong
ceiling. `BARS = 3000` is not a market fact; it is what Binance serves on daily bars.
Asked for more, Yahoo returns **24 807 daily bars of S&P 500 back to 1927-12-30**, so
h = 63 yields 392 observations and h = 252 yields 97.

That is the execution side of the registration being wrong rather than the hypothesis:
18 of the 42 registered cells had been made impossible by a crypto data limit applied
to every series. The correction was made **before any momentum result on the long
history had been seen**, which is what separates it from widening a grid after
noticing which cells came out positive.

| h (bars) | observations | phi | t | gate 1 |
|---|---|---|---|---|
| 1 | 24 806 | −0.0127 | −0.87 | no |
| 5 | 4 960 | −0.0075 | −0.29 | no |
| 10 | 2 479 | +0.0376 | +1.07 | no |
| 21 | 1 180 | −0.0275 | −0.69 | no |
| 63 | 392 | +0.0414 | +0.55 | no |
| 126 | 195 | +0.1027 | +1.17 | no |
| 252 | 97 | −0.0108 | −0.08 | no |

**This is the canonical test of the canonical anomaly, on the canonical dataset, over
99 years, and it finds nothing.** Time-series momentum was registered with an
economic reason, measured at seven horizons on 24 806 daily observations, and is
refuted at every one of them. The strongest reading is +0.103 at h = 126, against a
bar of 3.10.

Across the whole 42-test grid — crypto, gold, FX, a single equity, and the index —
**nothing passes gate 1.** Several pass gates 2 and 3, which is what a real effect
would look like if it were too small to survive multiplicity; none is large enough.

One thing in those numbers must not be misread. The long-horizon `net@10bp` figures
are large and positive — +362% at h = 252 — while `phi` at that horizon is *negative*.
The traded rule goes long in an index that rose 4 000-fold over the sample, so it
collects the index's drift. Drift is not momentum, and a positive net on a rising
index is the market being paid, not an edge being found.

### The range breakout, registered from a paper and measured here

A 2024 note — Kusaev, *Progressive Economy* 4, DOI 10.54861/27131211_2024_4_83 —
describes a head-and-shoulders on BTC and states the tradeable part mechanically: the
signal is the neckline break, and the neckline is the range boundary.

**Its rule was taken. Its conclusions were not.** The note contains no returns, no
sample and no test; its central claim is that the patterns "do not contradict" wave
theory, which is a statement about interpretability rather than about edge, and the
author notes the same patterns can be read several ways. That is the reason they need
measuring rather than reading. So the operational claim was registered and measured
with the instruments here:

> Long when the close exceeds the highest high of the previous 20 bars, short when it
> falls below the lowest low, flat otherwise, held 5 bars. Both directions — the
> engine's default long/flat mode would make half of every breakout unreachable.
> Parameters by convention, not tuned. 3 timeframes on each of BTC and ETH, so six
> tests. Gate 1: net positive after 10 bps per fill and t > 2.70. Gate 2: both halves
> of the series net positive.

```bash
$env:EDGE_BREAKOUT="1"
python -m tools.edge_ceiling 1d BTC/USDT,ETH/USDT 3000 4
```

| series | tf | bars | signals | gross | net @10bp | t | gate 1 | gate 2 |
|---|---|---|---|---|---|---|---|---|
| BTC/USDT | 1d | 3 000 | 191 | +97.3% | **+64.9%** | +0.73 | no | no |
| BTC/USDT | 4h | 20 004 | 1 134 | +339.4% | **+143.2%** | +1.99 | no | **yes** |
| BTC/USDT | 1h | 30 000 | 1 606 | +32.8% | **−258.3%** | −2.36 | no | no |
| ETH/USDT | 1d | 3 000 | 176 | +258.9% | **+227.7%** | +1.69 | no | no |
| ETH/USDT | 4h | 20 004 | 1 150 | +311.1% | **+112.5%** | +1.33 | no | **yes** |
| ETH/USDT | 1h | 30 000 | 1 575 | +39.0% | **−244.6%** | −1.60 | no | no |

**Nothing passed, and that is the verdict.** The rule does not clear the registered bar
at any timeframe on either asset.

Two things in that table are worth reading carefully, because they are the strongest
thing in this repository and they are still not a result.

**The 4-hour rows are the closest anything has come.** Net +143.2% on BTC and +112.5%
on ETH over 20 004 bars, positive after costs, and — the part that matters — positive
in *both halves* of both series. Two independent instruments agreeing in direction,
each consistent across its own history, with t = 1.99 and 1.33 against a bar of 2.70.
That is what a small real edge looks like, and it is not enough. Choosing 4h *because*
it is the row that passed gate 2 while 1d and 1h did not would be selection across the
six registered tests, which is the same move as tuning the lookback until something
clears.

**The rule dies where turnover rises, and the arithmetic is exact.** At 1h the gross
collapses to +32.8% and +39.0% while 10 bps per fill on 1 600 signals takes it to
−258.3% and −244.6%. Same rule, same parameters, three times the frequency, negative
outcome. This is the clearest demonstration in the project that for a cost-bearing
strategy, *timeframe is a risk parameter and not a display setting*.

On the note itself: its own claim, at its own asset and its own timeframe, reads
+64.9% net with t = 0.73. The operational idea survives as a testable rule; the paper
does not show it works.

### The one non-subjective part of wave theory is a moving average

An RBK Investments explainer from June 2022 offers the method's objective substitute:
the Elliott Wave Oscillator, "the difference of moving averages over five and
thirty-four periods … essentially the histogram of MACD", and it states plainly that
the oscillator "does not use the wave-detection rules". The trade is to be long while
it is above zero on a trough-to-peak leg.

**So the rule registered and measured here is a moving-average crossover, and its
result belongs to that rule and not to wave theory.** The periods are the article's.

A property of the rule was established before any real data was run, and it decides
how the result may be read: on a synthetic series rising **36-fold**, the sign of
MA(5) − MA(34) nets **+2.2%** after costs. It flips about every twelve bars and
spends half its time short, so it forfeits nearly all of a known drift. Whatever it
earns on BTC did not come from holding an uptrend.

| series | tf | bars | fills | gross | net @10bp | t | gate 1 | gate 2 |
|---|---|---|---|---|---|---|---|---|
| BTC/USDT | 1d | 3 000 | 247 | +359.0% | **+334.3%** | +1.90 | no | **yes** |
| BTC/USDT | 4h | 20 004 | 1 799 | +320.6% | +140.7% | +0.68 | no | no |
| BTC/USDT | 1h | 30 000 | 2 767 | +14.5% | **−262.2%** | −3.11 | no | no |
| ETH/USDT | 1d | 3 000 | 215 | +427.1% | **+405.6%** | +1.73 | no | **yes** |
| ETH/USDT | 4h | 20 004 | 1 799 | +301.2% | +121.3% | +0.47 | no | no |
| ETH/USDT | 1h | 30 000 | 2 811 | −10.1% | **−291.2%** | −2.56 | no | no |

**Zero of six passed.** The registered verdict is negative.

But the daily rows are the closest anything in this repository has come to its bar:
**+334.3% on BTC and +405.6% on ETH after costs, positive in both halves of both
series, t = 1.90 and 1.73 against 2.70.** Two independent instruments, each
consistent across its own history.

And there is a mechanism, which is the part that matters. The rule is nearly blind to
drift — proved above. So its gain cannot be drift. It is timing, and daily crypto
return autocorrelation is significantly **negative** at −0.0677 on BTC and −0.0721 on
ETH. A rule that goes short when the short average is below the long one is aligned
with that reversal rather than against it. **Two independent measurements explain each
other**, which is the first time in this project that has happened.

The cost arithmetic separates the rows. At 1d the rule fills 247 times over 3 000 bars
— 0.08 fills per bar — against the bar-flipper's 1.07, so 10 bps is survivable. At 1h
it fills 2 767 times and the same rule loses 262% and 291%. Slow enough to keep the
reversal, fast enough to pay for it: that is the whole trade-off, and 1h is past it.

The honest position: this is a lead with a mechanism, not a finding. The gate says no.

**The next step is to test the mechanism, not the rule.** If the gain really comes from
the measured negative autocorrelation, then two things must hold, and both are
falsifiable:

1. the rule's P&L should rank in the same order as the measured φ across instruments
   and timeframes;
2. the gain should vanish where φ is near zero, and appear where φ is significantly
   negative.

If the ranking does not follow φ, the mechanism is wrong and the +334% is something
else. Registered before that test, like every other one here.

### The mechanism test failed, and it was the right thing to run

| series | phi | t(phi) | rule net | t(rule) | buy-and-hold |
|---|---|---|---|---|---|
| BTC/USDT | −0.0677 | −2.26 | +334.3% | +1.90 | +1068.3% |
| ETH/USDT | −0.0721 | −2.61 | +405.6% | +1.73 | +503.1% |
| ^GSPC | **−0.1261** | −1.91 | **−2.3%** | −0.04 | +281.4% |
| GC=F | −0.0289 | −1.28 | −0.8% | −0.01 | +259.7% |
| EURUSD=X | +0.0075 | +0.33 | −36.2% | −1.40 | +2.4% |
| AAPL | −0.0478 | −1.20 | +24.8% | +0.24 | +1096.9% |

**Spearman(phi, rule net) = −0.429 — the wrong sign.** Predictions 3 and 4 failed.
The rule paid on no series where the market fell, and every series it paid on had risen
500–1100%. "Drift explains it" is also wrong: on ^GSPC, up 281%, the rule returned
**−2.3%**, and on gold, up 260%, **−0.8%**.

So the description of this result as "a lead with a mechanism" was wrong and is
withdrawn. It was a long-biased rule printing the market's direction.

### Prediction 4 demanded a falling market, so four were named in advance

Nothing in the sample fell hard enough to test it, so the periods were fixed before
looking rather than searched for — searching history for windows where the rule worked
returns a pass every time.

| series | period | bars | rule net | t | buy-and-hold |
|---|---|---|---|---|---|
| ^GSPC | 1929-08 → 1932-11 | 835 | **+88.4%** | +1.11 | **−77.5%** |
| ^GSPC | 2007-10 → 2009-03 | 378 | **+13.6%** | +0.31 | **−48.4%** |
| BTC/USDT | 2018-01 → 2018-12 | 501 | **+90.1%** | +0.88 | **−11.3%** |
| BTC/USDT | 2021-11 → 2022-11 | 1 000 | +130.0% | +1.14 | +81.6% |

**Four of four, criterion held.** The rule returned +88.4% through the Great
Depression, while the S&P lost 77.5%, and +13.6% through the 2008 crash against a 48.4%
fall. **Drift cannot manufacture that** — this is the first result in the project that
has survived a test built specifically to destroy it.

What it is not, yet, is a finding. Individually the t-statistics are weak — the largest
is +1.14 — and the sign count is 4 of 4, which is `p = 0.0625` one-sided. Marginal,
not significant. The φ mechanism is separately refuted: the rank correlation came out
with the wrong sign, so what MA(5) vs MA(34) exploits is not the 1-bar autocorrelation
measured earlier. Possibly it is a different horizon — a 5-versus-34 comparison is not
a one-bar question — but that is a guess, and it gets its own registration rather than
being asserted here.

**Position: drift is ruled out; the mechanism is open; significance is not reached.**
The rule earns money in rising and falling markets, on four named periods including
the hardest available test. That is worth a proper registered test on periods not used
here, and it is emphatically not worth a lookback sweep — a rule that has survived
this much and then been tuned is the same artefact under a better name.

### Confirmation on untouched data — failed

Everything above came from BTC and ETH, whose drift and autocorrelation were already
known and discussed before the rule was run. Results that were *expected* to look like
that cannot also confirm it. So the confirmation ran on two axes that formed no part of
the impression: five instruments never used anywhere above, and three bear periods
never used above.

| fresh instrument | bars | rule net | t | buy-and-hold |
|---|---|---|---|---|
| SOL/USDT | 2 247 | +658.0% | +2.40 | +3546.5% |
| XRP/USDT | 3 000 | −64.6% | −0.22 | +238.1% |
| DOGE/USDT | 2 650 | +912.1% | +1.81 | +2371.3% |
| ^NDX | 10 332 | **−25.7%** | −0.16 | **+27 612.2%** |
| ^RUT | 9 841 | +67.5% | +0.54 | +1584.8% |

| fresh bear period | bars | rule net | t | buy-and-hold |
|---|---|---|---|---|
| 1968-01 → 1982-12 | 3 761 | **−85.0%** | −1.63 | **+46.3%** |
| 2000-03 → 2002-10 | 655 | +26.3% | +0.74 | −43.7% |
| 2018-10 → 2020-03 | 371 | +29.1% | +1.40 | −23.5% |

**Criterion A failed: 3 of 5 positive, none significant. Criterion B held: 2 of 3.**

Not confirmed, and the failure is informative rather than marginal. The two fresh
instruments that paid are the two with the most violent drift — SOL at +3546% and
DOGE at +2371% — where the rule captured 18% and 38% of a rise, the same ratio as on
BTC. XRP lost. And on the Nasdaq 100, up **27 612%**, the rule returned **−25.7%**:
it captures essentially nothing of an equity drift, consistent with the −2.3% it
made on the S&P since 1929.

A registration error is worth recording too. The 1968–1982 window was named here as a
"secular bear", and the market was **+46.3%** across it — the −46% crash was inside the
window, not across it. The rule lost 85% over fourteen years of grinding upward, which
is the whipsaw this rule is structurally prone to, and the same thing it did over the
full S&P history. The tool's own buy-and-hold column caught the mislabelling, which is
the argument for printing it.

So the sequence now reads: a promising daily result, a falsification test it survived,
and a confirmation test it did not. The +334% and +406% were the two best-known,
highest-drift crypto assets, and the effect does not travel.

## Operator session token

## The indicator panel: 56 tests, nothing clears

The request was to add more oscillators. Taken literally — try them all, keep the
best — that is best-of-N, and the size of the effect was already measured here: at a
t-bar of 2.70 across 12 tests, about 0.04 pass by chance. So this was framed as a
**panel study** instead of a search, in a way that answers the real question (is there
signal in technical indicators at all) and survives the answer being no.

Three properties make it a measurement rather than a sweep:

1. **One functional form for all.** Each indicator's output becomes a trailing 60-bar
   z-score and the position is its sign. Nothing about the rule depends on which
   indicator it is reading.
2. **Both signs tested for every indicator.** Whether a tool is a trend or a reversion
   indicator is not known in advance, so scoring only the favourable direction would
   smuggle the answer in. This doubles the panel and makes the multiplicity explicit.
3. **Library defaults only.** Tuning each of fourteen indicators would turn a
   fourteen-test panel into a search over thousands of configurations.

Registered bar: t > 3.10, one-sided Bonferroni for 52 tests. The library turned out to
hold fourteen usable oscillators rather than thirteen, so the real grid is **56** and
the bar is 3.11. Recorded rather than rounded; no verdict changes.

| indicator | BTC +sign | BTC −sign | ETH +sign | ETH −sign |
|---|---|---|---|---|
| rsi-14 | +155.7% (t +0.89) | −265.5% | +313.3% (t +1.35) | −445.1% |
| macd-12-26-9 | +232.6% (t +1.33) | −308.8% | +105.3% (t +0.46) | −185.5% |
| bollinger-20-2 | +117.7% (t +0.67) | −265.1% | +286.4% (t +1.24) | −427.8% |
| stoch-14-3 | +299.5% (t +1.71) | −398.1% | +424.3% (t +1.83) | −526.5% |
| cci-20 | +102.4% (t +0.58) | −227.8% | +231.2% (t +1.00) | −363.8% |
| williams-14 | +200.6% (t +1.15) | −361.6% | +84.8% (t +0.37) | −260.6% |
| roc-12 | +48.5% (t +0.28) | −187.5% | +259.5% (t +1.12) | −396.1% |
| adx-14 | +317.7% (t +1.82) | −400.3% | +450.5% (t +1.95) | −539.1% |
| sar | +24.1% (t +0.14) | −119.1% | −0.8% (t −0.00) | −88.2% |
| keltner-20 | +190.1% (t +1.09) | −313.9% | +389.1% (t +1.68) | −523.3% |
| heikin-ashi | −33.3% (t −0.19) | −601.3% (t −3.43) | +73.9% (t +0.32) | −702.5% |
| vwap-20 | +112.8% (t +0.64) | −232.6% | +393.4% (t +1.70) | −510.8% |
| sma-20 | +115.7% (t +0.66) | −237.1% | +376.1% (t +1.62) | −495.9% |
| ema-50 | +344.3% (t +1.97) | −409.7% | +461.2% (t +2.00) | −531.4% |

**56 rows run, 26 net positive, 0 clear the bar.** The largest t is +2.00.

**And the table says something more useful than the count.** Every single +sign row is
positive and every single −sign row is negative. That is not fourteen independent
findings — it is **one finding expressed fourteen times**: the sample went up. The panel
has one real degree of freedom, long versus short, and the indicators are not adding
any information to it. The apparent cross-sectional agreement is agreement about the
market, not about the tools.

The strongest row is `ema-50` at +344.3% and +461.2% — which is the MA crossover from
the previous commit, re-derived. That is a useful pipeline check: two independent code
paths arriving at the same number. It also settles what that result was. `sign(EWO)`
is `sign(close − EMA)`, so the +334% and +406% **were** long exposure, and the panel
shows it directly by reproducing them as its top row.

Building this panel cost three of my own bugs, all of the same shape as the ones before:
unpacking parallel series by iteration as if they held scalars, a keyword-only argument
passed positionally, and a helper that caught every exception and printed nothing — so
eight of fourteen indicators failed silently and the panel reported itself as complete.
A half-panel that looks whole is worse than no panel, because the missing half is the
part nobody checks. The helper now prints the failure and its reason, and the run
reports `56 of 56`.

**Panel result: no indicator in this library carries measurable signal on daily BTC or
ETH, in either direction, at library-default parameters.** The value of the study is
that corrected count, not a winner.

### Volatility-expansion reversal: a real signal, measured, and priced

The one direction the measurements point at: fade a move larger than 2 sigma, holding
2 or 10 bars. Registered before the run, with the trigger at the conventional 2 sigma
and the direction taken from the sign of the measured phi rather than from testing
both ways. Eight tests, Bonferroni bar t > 2.90.

**The primitives are the finding.**

| series | tf | bars | phi | t(phi) | mean after a 2σ move | t | breakeven |
|---|---|---|---|---|---|---|---|
| BTC/USDT | **4h** | 20 004 | **−0.0438** | **−3.44** | −0.00154 | −1.48 | **10.1 bps** |
| BTC/USDT | 1h | 29 999 | −0.0164 | −1.72 | +0.00002 | +0.08 | 1.3 bps |
| ETH/USDT | **4h** | 20 004 | **−0.0346** | **−3.01** | −0.00074 | −0.61 | 10.1 bps |
| ETH/USDT | 1h | 29 999 | +0.0004 | +0.04 | +0.00034 | +0.83 | 0.0 bps |

**The negative autocorrelation is stronger and far more significant on 4h than on
daily** — t = −3.44 against −2.26 on BTC, −3.01 against −2.61 on ETH, over 20 004
observations. This is the most solid single number produced anywhere in this project:
a short-horizon reversal effect in crypto that is not noise. It is also the answer to
why daily testing never found it — the effect lives at a horizon the daily grid cannot
see, because 20 004 bars are 8× more than daily sampling of the same history gives.

The rule still loses, and the table says exactly why:

| series | tf | hold | signals | turns/bar | gross | net @10bp | t |
|---|---|---|---|---|---|---|---|
| BTC/USDT | 4h | 2 | 6 667 | 0.667 | +52.2% | −1281.2% | −7.74 |
| BTC/USDT | 4h | 10 | 1 819 | 0.182 | −271.5% | −635.2% | −3.54 |
| ETH/USDT | 4h | 2 | 6 654 | 0.665 | −57.4% | −1388.1% | −6.52 |
| ETH/USDT | 4h | 10 | 1 817 | 0.182 | −212.3% | −575.6% | −2.30 |

Zero of eight clear the bar. But the reason is now a **price, not an absence**:

- the reversion is worth about **15 bps per trade** (the conditional mean is 0.00154);
- a round trip costs about **20 bps** at the 10 bps assumed here;
- capturing it at a 2-bar hold needs 0.667 trades per bar, which is what turns a
  15 bps edge into a −1281% bill;
- holding 10 bars cuts turnover 3.7× but *destroys* the gross (−271.5%), because the
  reversion is a one-bar effect and the remaining nine bars are pure drift.

**That is a materially different position from everything else in this file.** Every
previous negative was "the effect is not there, or not tradeable". This one is: the
effect is there at t = −3.44, it is worth a measured 15 bps per trade, and it loses to
a 20 bps round trip. The gap is a pricing problem, and pricing problems have a known
lever — a lower all-in cost, or a slower instrument where the same edge is captured
with fewer fills. The breakeven is 10.1 bps per fill against a 4–10 bps market, so a
maker tier or a wider instrument sits inside it.

The equity intraday leg could not run: Yahoo raised `TimeframeMismatchError` on 4h and
returned nothing for 1h, so the 99 years of daily history is not available at the
horizon where the effect was found.

Building this cost one bug of the worst kind in this repository's history: the running
cost total was reset on every position open, leaving net equal to gross, and the rule
reported **+120% on a series with zero autocorrelation** because the fills it was
being charged for had been forgotten. Only the self-check against a series with a
known answer caught it. It is now pinned by a test that requires costs to accumulate,
to scale with the rate, and to leave net strictly below gross.

895 passed.

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
