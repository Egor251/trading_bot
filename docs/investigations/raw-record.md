# Raw record: every registration, criterion, number and output

The evidentiary layer. [`REGISTER.md`](REGISTER.md) is the index and carries the
verdict for each row here; `log.md` is the summary, `narrative.md` is the reasoning.
This file is what was actually registered and what came back, in order, without
compression — so that nothing rests on a retelling.

Session 1 entries are marked **[S1]** and come from the summary carried as working
context: criteria and numbers survive, exact tool output does not. Session 2 entries
are direct.

---

# SESSION 1

## S1-1 · Predictability ceiling

Registered: estimate AR(1) with Newey-West and Lo-MacKinlay variance ratio, pooled
and across windows, on crypto. Criterion: a positive φ clearing the detection floor.

```
BTCUSDT   4h   n=20 003
ETHUSDT   4h   n=20 003
DOGEUSDT  4h   n=15 894
SOLUSDT   4h   n=13 480
```

Resolvable floor at n=749: 0.0716. A window of 749 bars cannot distinguish φ = 0.05
from zero regardless of what the market does.

Verdict: negative. No positive φ clearing the floor.

## S1-2 · Power sweep

Registered criterion: the smallest injected φ detectable above the noise floor by
more than 5 pp.

```
φ       snr     detection lift
0.00    0.000        +0.0 pp
0.05    0.319        +6.5 pp     ← registered floor
0.10    0.638       +14.3 pp
0.20    1.277       +32.0 pp
0.30    1.915       +48.7 pp
0.50    3.192       +71.4 pp
0.80    5.106       +80.0 pp
```

snr = φ · sqrt(2/π) · sqrt(per_window). Axis is ρ-based, not σ-based.

## S1-3 · Null calibration

```
structure     cleared / total   rate    deviation from nominal 10%
independent       2071 / 16800   12.3%      +2.3 pp
market            3280 / 16800   19.5%      +9.5 pp
serial            3696 / 16800   22.0%     +12.0 pp
```

Trials 200 × 21 strategies × 4 windows, null of 40 seeds.

Per-window detection against window count, at φ = 0.30:

```
windows   4      8      16     32
rate    70.5%  68.5%  68.7%  68.5%
```

Flat, and structurally so: p90 is judged inside one window against one null.

Bonferroni reachability, exact arithmetic:

```
windows   best p = 2^-W   threshold 0.05/21 = 0.00238
   4          0.06250         unreachable
   8          0.00391         unreachable
  10          0.00098         reachable
  16          0.00002         reachable
  20          0.00000         reachable
  32          0.00000         reachable
```

## S1-4 · Ten-window design

Registered: 10 windows minimum, because below that Bonferroni at S = 20–21 is
unreachable by any outcome including a rigged one.

Result: 28/210 raw, 30/210 after demeaning, **zero at 10/10**.

## S1-5 · Asset-class sweep

Registered: 12 instruments, 8 horizons. Result **0/12 positive, 9 of 12 negative**.

## S1-6 · Momentum at registered horizons

Data: 24 807 S&P daily bars back to 1927. Registered horizons: 1, 3, 6, 12, 24, 48,
120 months. Result: none clears. Pooled φ = **−0.0127**.

Not refuted — structurally untestable at the registered bar with the data available.

## S1-7 · Breakout from the Elliott paper

Source DOI 10.54861/27131211_2024_4_83. Rule extracted, conclusions discarded.
Registered: 6 variants. Result **0/6**.

## S1-8 · Indicator panel

Registered: 14 oscillators × 2 signs × 2 instruments = 56 tests.
Bonferroni bar 3.11. Result: **0 clear**.

## S1-9 · EWO — MA(5) vs MA(34)

Rule extracted from an RBC explainer. Rule only, not its conclusions.

```
BTCUSDT  +334.3%  daily
ETHUSDT  +405.6%  daily
```

**Down-market test, 4 periods named in advance** (Prediction 4 required a falling
market, so the periods were named before running):

```
through the Great Depression   EWO +88.4%   S&P −77.5%
```

Survived 4/4.

**Confirmation on fresh instruments: 3/5 held, none significant.**

**Panel reproduction:** appeared as the panel's `ema-50` row → long exposure, not a
signal.

## S1-10 · Mechanism test

The finding. Registered question: why would *anything* work here?

**4h negative first-order autocorrelation**
```
BTCUSDT  4h  n=20 004   t = −3.44
ETHUSDT  4h              t = −3.01
```
**[S2] measured later:** BTC 4h ρ₁ = −0.0436, BTC 1d ρ₁ = −0.0494.

## S1-11 · Tradability — breakeven curve

```
edge per trade            0.78 bps
round-trip cost         ~20 bps
shortfall                 25×
breakeven cost per fill    0.6 bps
```

**First statement was wrong:** the effect was described as ~15 bps/trade, "a pricing
problem". Corrected explicitly: 25× shortfall, not 1.3×.

## S1-12 · Bounce test

Conditional reversion vs spread proxy:

```
DOGE vs BTC   2.61×   BOUNCE CONFIRMED
```

But the *sign ordering* ran opposite to bounce → liquidations/funding, not spread.

---

# SESSION 2

## S2-1 · Engine statistics

`engine/simulation.py` additions: `ar1` (Newey-West), `variance_ratio` (Lo-MacKinlay,
overlapping-sums correction), `resolvable_phi` (1.96/sqrt(n)).

Newey-West widening measured, not asserted:

```
φ_true   naive SE   NW SE    ratio
0.00     0.014228   0.014173  0.9962
0.10     0.014137   0.014149  1.0009
0.25     0.013469   0.013811  1.0255
0.50     0.010893   0.012310  1.1301
0.80     0.005311   0.008334  1.5692
```

`resolvable_phi`: n=749 → 0.07162, n=748 → 0.07166, n=20004 → 0.01386, n=25000 →
0.01240.

## S2-2 · The first p-value — WRONG

Implementation: share of bootstrap means past the observed mean.

```
strong +3.0   ci [2.9200, 3.0850]  p = 0.975     ← wrong
null          ci [−0.0298, +0.0361] p = 0.99334  ← wrong
degenerate    p = 0.0                            ← wrong
empty         KeyError 'p_value'
```

Cause: bootstrap distribution centred on the observed statistic by construction.

Corrected — recentre on zero before block resampling:

```
strong +3.0    mean +3.0000  ci [+2.9200, +3.0850]  p=0.00166
null           mean −0.0002  ci [−0.0298, +0.0361]  p=0.99334
degenerate     mean +0.0000  ci [+0.0000, +0.0000]  p=1.00000
single obs     mean +5.0000  ci [+5.0000, +5.0000]  p=1.00000
empty          mean +0.0000  ci [+0.0000, +0.0000]  p=1.00000

false-positive rate on iid noise: 13/300 = 0.043   (target 0.05)
detection rate on shifted mean:  300/300 = 1.000
```

Floor `(1+count)/(1+n)`.

## S2-3 · Multiplicity — the wiring

`apply_multiplicity` in `engine/research_gate.py`. Policy:

```
require_multiplicity_correction   True
multiplicity_method               holm
multiplicity_alpha                0.05
```

Batch of 20, one row at p = 0.04:

```
universe_basis / isolated:   survives, adjusted p = 0.04
in batch of 20:              rejected, adjusted p = 0.8200000000000001
```

Method comparison on [0.041, 0.048, 0.012, 0.30]:

```
bonferroni            {'s0': 0.164, 's1': 0.192, 's2': 0.048, 's3': 1.0}
holm                  {'s0': 0.123, 's1': 0.123, 's2': 0.048, 's3': 0.3}
benjamini-hochberg    {'s0': 0.064, 's1': 0.064, 's2': 0.048, 's3': 0.3}
```

Unknown p-value: blocks, `p_value_adjusted: None`, blocker
`multiplicity_unknown_p_value`.

Unknown rows do not inflate N: 10 known + 10 unknown → same as 10 known alone.

Live snapshot after wiring: **all 20 strategies** gained `multiplicity_unknown_p_value`
(before the field existed on record).

## S2-4 · Reality Check, SPA, PBO

`reality_check`, `spa_test` (trim 0.10, studentized), `cscv_pbo` (splits 8, cap 512).

PBO known-answer cases:

```
one strategy has full-sample edge, 9 noise:  pbo = 0.748, worse_than_random = True
one strategy strongly dominant:               pbo = 0.000, worse_than_random = False
combinations_available = 487635, combinations_used = 512, splits_used = 512
```

**Trimming by performance was a no-op, measured:**

```
trim=0.00  p=0.0025  max=+12.678
trim=0.10  p=0.0025  max=+12.678
trim=0.20  p=0.0025  max=+12.678
trim=0.30  p=0.0025  max=+12.678
```

Byte-identical. Replaced with reliability trimming.

Calibration on noise: rejection rate < 0.15 over 60 runs.

## S2-5 · Cost decomposition

```
product config   fee 5.0 + slippage 2.0 = 7.0 bps per fill
research sweep   10.0 bps per fill (hardcoded, 18→19 call sites)
research is 1.43x the product's own cost model

total charged        7.00 bps/fill
fee (a transfer)     5.00 bps   71.4%
measured crossing    1.19 bps
residual slippage    0.81 bps
impact total         2.00 bps   28.6%
```

Unit trap, both readings:

```
vs research cost (10 bps/fill):  12.8x     ← wrong, mixed per-fill with per-trade
round-trip units:                25.6x     ← correct
vs product cost (7 bps/fill), impact only:  5.1x
measured spread alone (zero fee) vs 0.78:   1.5x   ← still fails
```

## S2-6 · Roll — EDGE_ROLL

Registered criteria, 13 tests, Bonferroni bar 3.00:

- G1 γ₁ < 0 at t < −3.00, on log returns at 4h
- G2 γ₁/var at 4h exceeds γ₁/var at 1d (scale-free discriminant)
- G3 |γ₁| ordered by liquidity: BTC < ETH < DOGE
- G4 implied spread vs ~20 bps round trip, under both constants

**First run — three bugs, found because the spread printed as 4175 bps:**

```
series      tz       n       gamma1       t  g1   spread bp    alt bp      rho1
BTC/USDT    4h   20003   -9.171e-06   -3.17   Y       4175.53   2952.55   -0.0436
BTC/USDT    1d    3336   -6.163e-05   -1.64   Y       4444.80   3142.95   -0.0494
ETH/USDT    4h   20003   -1.159e-05   -2.85   Y       3720.06   2630.48   -0.0346
ETH/USDT    1d    3336   -1.202e-04   -2.04   Y       4802.39   3395.80   -0.0577
DOGE/USDT   4h   15894   +3.732e-06   +0.15   .          0.00       0.00   +0.0059
DOGE/USDT   1d    2649   -1.949e-05   -0.14   Y       1345.32    951.29   -0.0045
SOL/USDT    4h   13480   -2.590e-05   -2.62   Y       4101.76   2900.38   -0.0421
SOL/USDT    1d    2246   -1.595e-04   -1.24   Y       4249.90   3005.13   -0.0452
```

Bugs: units (÷σ again, 200×); sign (`t < ROLL_GATE_T` with positive bar → always
true, 3/4 reported vs 1/4 correct); normalisation (`var**2` vs registered `var`).

**Second run, corrected:**

```
series      tz       n       gamma1       t  g1   spread bp    alt bp      rho1
BTC/USDT    4h   20003   -9.171e-06   -3.17   Y       60.57     42.83   -0.0436
BTC/USDT    1d    3336   -6.163e-05   -1.64   .      157.01    111.03   -0.0494
ETH/USDT    4h   20003   -1.159e-05   -2.85   .       68.10     48.15   -0.0346
ETH/USDT    1d    3336   -1.202e-04   -2.04   .      219.26    155.04   -0.0577
DOGE/USDT   4h   15894   +3.732e-06   +0.15   .        0.00       0.00   +0.0059
DOGE/USDT   1d    2649   -1.949e-05   -0.14   .       88.30     62.44   -0.0045
SOL/USDT    4h   13480   -2.590e-05   -2.62   .      101.78     71.97   -0.0421
SOL/USDT    1d    2246   -1.595e-04   -1.24   .      252.57    178.59   -0.0452

G1: 1/4 4h series with gamma1 < 0 at t < -3.0
G2: FAILS on all four (4h −0.0436 vs 1d −0.0494 — the longer horizon is MORE negative)
G3: FAILS (BTC 9.17e-06 < ETH 1.16e-05 < DOGE 3.73e-06 — ordering inverted)
G4: 60–252 bps, far above the ~20 bps round trip
```

**All four criteria failed.**

**Frequency walk — the decisive measurement:**

```
  tz       n   sigma %      rho1       t  implied bp
   1m    4999    0.0385   -0.0239   -0.74        1.19
   5m    4999    0.1095   -0.0254   -1.01        3.49
  15m    2975    0.1775   +0.0133   +0.38        0.00
   1h     999    0.3599   +0.0127   +0.28        0.00
   4h     999    0.7813   -0.0515   -1.47       35.47
   1d    1199    2.3875   -0.0473   -1.17      103.82
```

Estimator valid at 1m (1.19 bps), inflates beyond.

Estimator subtlety: i.i.d. ±amplitude series has γ₁ **exactly zero** — negative
covariance requires alternating sides.

```
alternating (defect=0.0)   gamma1 < −0.00005, spread > 0
iid sides  (defect=0.5)   gamma1 ≈ 0.0,          spread = 0.0
```

## S2-7 · Cost single source

`cost_model_from_config`. Path is `services.phantom_service` (also accepts
`trading.phantom_service`, and the block itself).

```
product fee 5.0 + slippage 2.0 = 7.0
fallback (absent block) = DEFAULT_FEE_BPS 5.0 / DEFAULT_SLIPPAGE_BPS 2.0
```

18 hardcoded `cost_bps=10.0` → `COST_BPS_PER_FILL = _shipped_cost_bps()`.

Test: no call site passes a numeric literal; variables allowed (grid sweeps vary cost
on purpose).

## S2-8 · Cost reconciliation wiring

`ops_monitor_service`: `cost_reconcile_every_sec` 3600, `cost_reconcile_min_samples`
30, `cost_reconcile_window` 500.

Two bugs during wiring:
- `await core.config.get()` with no key → TypeError, broke service load; caught by
  `test_ai_analyst_plugin.py` which boots every component.
- Passing the `services.phantom_service` **block** returned the **fallback** — which
  equals the shipped values, so every test passed while the wiring was dead. Fixed by
  accepting the block directly; test now feeds a different config and asserts the
  model changes.

Behaviour: a fill without a reference price is **excluded, not zero-filled**.
`sufficient: false` is a real state.

## S2-9 · Noise floor recalibration

Injected measured φ into null, market factor and strategies alike; marginal variance
held fixed by `sqrt(1 − φ²)`.

Registered values: 0.0, −0.044 (4h), −0.049 (1d), −0.068 (earlier 1d, adverse case),
+0.050 (positive control).

```
 null phi     rate      se   vs base
   +0.000    19.0%   0.30     +0.0pp
   -0.044    21.8%   0.32     +2.8pp   moved > 2se
   -0.049    21.8%   0.32     +2.8pp   moved > 2se
   -0.068    21.8%   0.32     +2.9pp   moved > 2se
   +0.050    21.8%   0.32     +2.9pp   moved > 2se
```

Supported at ~9 se. Floor **21.8%, not 19.5%**.

Power ladder re-run against measured null:

```
 phi   vs iid null   vs measured null   +5pp?
0.00       +0.0 pp            +0.0 pp   NO
0.05       +6.5 pp            +7.2 pp   yes
0.10      +14.3 pp           +15.4 pp   yes
0.20      +32.0 pp           +33.5 pp   yes
0.30      +48.7 pp           +50.4 pp   yes
0.50      +71.4 pp           +72.4 pp   yes
0.80      +80.0 pp           +78.9 pp   yes

smallest edge detected, iid null      : phi = 0.05
smallest edge detected, measured null : phi = 0.05
```

Detection floor does not move.

## S2-10 · Batch verdict

Four tests on one batch. Universe test: 8 strategies × 20 lifetime variants = **160**.

```
noise batch:        RC p=0.3142  max=+1.759  reject=False  PBO=0.988
one dominant:       RC p=0.0025  max=215.297 reject=True   PBO=0.000
interchangeable:    RC p=0.0025  max=+7.885  reject=True   PBO=0.965
```

**Verdict direction was inverted first:**

```
noise batch     → unstable_selection   (should be no_batch_edge)
one dominant    → no_batch_edge        (should be batch_edge_stable)
```

Fixed: `passes = rc.reject and spa.reject`.

`not_evaluable` was reported as `no_batch_edge` on the live snapshot — 20 strategies
looked like refutations when no test had run. Now `batch_not_evaluable`.

Added fields: `oos_pnl_by_fold` through walk_forward → digest → clearance → gate.

## S2-11 · The leak

`phantom_service.run_walk_forward` called `record_digest_clearance(result["digest"])`
with no path → `DEFAULT_CLEARANCE_PATH`, absolute, inside the repository. Under
`except: pass`. No switch.

Result: 20 entries written to the live store; `variants_tried_lifetime` inflated
3 → 12.

Fixed with `persist_clearance: bool = True` (default unchanged), false in the tool.

## S2-12 · Lineup verdict

First run, 5 folds — **H3 unmeasurable**:

```
PBO ok=False  reason=insufficient_series   (5 folds < 8 splits)
```

Also: the roster filter cut 19 of 20 strategies (`ensemble.strategies` ships with 1),
and `build_sandbox_config` disables all plugins outside `enable_plugins`, so every run
raised "strategy plugin not loaded" while looking like a clean negative.

12 folds, 20 strategies, ETH only:

```
dual_thrust                folds= 12 mean=  +15.676 p= 0.0200
momentum_roc               folds= 12 mean=  +61.061 p= 0.2807
keltner_breakout           folds= 12 mean=  +70.894 p= 0.0959
macd_trend                 folds= 12 mean=  +35.416 p= 0.5145
supertrend_follow          folds= 12 mean=  +21.370 p= 0.5455
turtle_breakout            folds= 12 mean= +100.795 p= 0.0050
williams_r_reversion       folds= 12 mean=  +25.454 p= 0.6174
cci_reversion              folds= 12 mean=   -7.856 p= 0.8591
bollinger_reversion        folds= 12 mean=  +22.704 p= 0.6883
ema_cross                  folds= 12 mean=  +48.042 p= 0.4216
rsi_reversion              folds= 12 mean=   +0.648 p= 0.9890
adx_di_trend               folds= 12 mean=  +59.956 p= 0.1868
heikin_ashi_trend          folds= 12 mean=  +41.634 p= 0.5255
ma_cross_example           folds= 12 mean=  +46.234 p= 0.3696
parabolic_sar_follow       folds= 12 mean=   +0.879 p= 0.9930
stochastic_reversion       folds= 12 mean=  -38.693 p= 0.3367
volatility_breakout_example folds= 12 mean=  +75.488 p= 0.1489
volume_impulse_example     folds= 12 mean=  -48.937 p= 0.4286
vwap_reversion             folds= 12 mean=  +14.604 p= 0.7812
mean_reversion_example     folds= 12 mean=   -1.586 p= 0.9520

H1 evaluable: HOLDS   (20 series, universe 126)
H2 no strategy clears: HOLDS
H3 PBO high: HOLDS
universe 246   best dual_thrust
RC p=0.0020 reject   PBO 0.933   verdict unstable_selection
```

**5 folds vs 12 folds, same data:**

| strategy | 5 folds | 12 folds |
|---|---|---|
| momentum_roc | −28.326 | **+61.061** |
| volume_impulse_example | −110.912 (p=0.0010) | −48.937 (p=0.4286) |
| turtle_breakout | +74.123 (p=0.0500) | +100.795 (p=0.0050) |
| macd_trend | +79.081 | +35.416 |
| best | turtle_breakout | **dual_thrust** |

Three tickers (BTC, ETH, SOL), 12 folds:

```
universe 60
best heikin_ashi_trend
RC p=0.0020 reject   max_stat 4.131
SPA p=0.0020  18 strategies after trimming
PBO 0.869  495 splits  worse_than_random
verdict unstable_selection
```

22 strategies with control and time_series_momentum:

```
universe 247   22 series
RC p=0.0020   PBO 0.895   best heikin_ashi_trend
H1/H2/H3 all HOLDS
```

## S2-13 · Control

```
random_baseline  explored=1  available=None  mean=+15.378  p=0.15284715284715283

blockers (6):
  ci_excludes_zero, profit_factor, mean_oos_pnl, tickers_robust,
  multiplicity, batch_unstable_selection
live_ok=False   observe_ok=False
```

Must fail. If it ever passes, every number is void.

## S2-14 · Universe / schema discovery

Inventory pass 1: 16 with schema, 7 without. Pass 2 (stricter): 14 and 9 — and
`supertrend_follow` among the misses, while `plugin.py` line 26 defines
`CONFIG_SCHEMA` on the class.

Cause: `StrategyPlugin` declares `CONFIG_SCHEMA = {}`; `dir()` is alphabetical;
`StrategyPlugin` sorts before `SupertrendFollowPlugin`; discovery stopped on the
empty base; `if not schema` read `{}` as absent. **Missed strategies depended on
alphabetical position.**

After the fix:

```
with schema: 20   without: 3
without: ['random_baseline', 'test_strategy', 'time_series_momentum']
full available space (cap 36): 488
never searched: 428  = 88%
```

Per-strategy before the fix:

```
dual_thrust      searched 3 of 36   untried 33
keltner_breakout searched 3 of 36   untried 33
macd_trend       searched 3 of 36   untried 33
parabolic_sar_follow searched 3 of 36 untried 33
rsi_reversion    searched 3 of 36   untried 33
stochastic_reversion searched 3 of 36 untried 33
adx_di_trend     searched 3 of 16   untried 13
(7 more at 16; heikin_ashi_trend at 4)
```

`time_series_momentum` schema added: lookback (20–504), vol_window (5–120),
target_vol (0.01–0.50), quantity, class_code.

**Universe readings, in the order they were taken — two of them wrong:**

```
(1) universe_basis=explored    universe 60
(2) universe_basis=available   universe 234      ← wrong, twice
(3) universe_basis=explored    universe 64
(4) universe_basis=available   universe 727      ← correct
```

Reading (2) was not the schema space. `variants_available` was absent from the record,
so `variants_available_lifetime` fell back to the explored count and accumulated it
across four runs (4 × 3 = 12, × 20 = 234). Then — after the field landed — it was
computed under the run's own `max_variants` cap of 12, so it reported **12 for every
strategy** while the schemas permit 16 or 36, and 12 × 22 ≈ 247.

Corrected to size the available space at the schema's own bound (36). Reading (4) is
the real one, and it is **11.4× the explored denominator**.

## S2-14b · `variants_available` — CLOSED

Two faults, both found only by checking the record on disk rather than trusting the
arithmetic:

1. **Written nowhere.** `record_digest_clearance` accumulated the cumulative field and
   never stored the per-run value. Computed → read → consumed → absent.
2. **Sized by the wrong cap.** `max_variants` is the caller's speed choice, so the
   available space could never exceed the explored one. A field that always equals the
   searched count is worse than no field: it looks measured.

Pinned by tests asserting the value is on disk and survives a reload — the batch
arithmetic worked the entire time the record was empty.

## S2-19 · Error catalogue, final

| # | error | how it surfaced |
|---|---|---|
| 1 | `_interval_days("1d")` = 1.0 → 300 requested, ~206 returned | silent truncation |
| 2 | cost total reset per position open → net == gross, +120% on a zero-ρ series | known-answer check |
| 3 | research 10 bps/fill vs product 7 | decomposing costs |
| 4 | bootstrap p from observed mean's position in its own bootstrap | p=0.975 for mean 3.0 |
| 5 | `t < BAR` with positive bar, negative t | column passed every negative reading |
| 6 | spread divided by σ again | 4175 bps = 42% |
| 7 | `var**2` vs registered `var` | contradicted registered text |
| 8 | verdict direction inverted | noise batch passed |
| 9 | trimming worst performers from a maximum | byte-identical at trim 0 and 0.3 |
| 10 | `not_evaluable` reported as `no_batch_edge` | live snapshot, 20 strategies |
| 11 | `phantom_service` writes to live gate unconditionally | inspected store before promoting |
| 12 | passing the config block returned the fallback | values identical to shipped |
| 13 | `config.get()` with no key → TypeError | service load test |
| 14 | `CONFIG_SCHEMA = {}` placeholder, discovery alphabetical | two passes disagreed |
| 15 | `resolvable_phi` as significance test on fat tails | t=8.54 below a 0.0352 floor |
| 16 | per-fill compared with per-trade | 12.8× vs 25.6× |
| 17 | **`variants_available` never written** | **CLOSED — entry had None** |
| 18 | **`variants_available` sized by the run's cap** | **CLOSED — reported 12, schema permits 36** |

**Shared signature:** wrong in a plausible direction, plausible units, exactly where a
reader would not look twice. Errors 17 and 18 are the purest instance — both invisible
to any test that exercised the arithmetic, and both only visible by reading the
persisted record.

## S2-15 · MOEX

Read-only, `aiomoex`, TQBR. `available` is a property, not a method.

```
SBER   n=3402  98.79 → 282.22   σ 2.13%  ρ1 +0.0046  t  +0.24  floor 0.0336
GAZP   n=3099 144.40 →  96.17   σ 2.12%  ρ1 +0.0142  t  +0.28  floor 0.0352
LKOH   n=3402 1922.0 → 5570.0   σ 1.89%  ρ1 −0.0089  t  −0.31  floor 0.0336
GMKN   n=3095 7029.0 → 118.60   σ 8.48%  ρ1 −0.0149  t  −8.54  floor 0.0352
ROSN   n=3099 240.36 → 344.85   σ 2.09%  ρ1 +0.0567  t  +1.72  floor 0.0352

negative with |t| > 3.0            : 1 of 5  ['GMKN']
negative beyond the 1/sqrt(n) floor : 0 of 5  []
the two criteria disagree on: ['GMKN']
```

First run used the resolution floor and reported **0 of 5** — wrong on GMKN:
|ρ₁| = 0.0149 below a floor of 0.0352 while |t| = 8.54; a t of 8.5 implies SE ≈
0.0017, ten times below 1/sqrt(n). `resolvable_phi` assumes homoskedasticity; equity
returns are fat-tailed.

No costs applied. MOEX charges commission plus a rate in price units.

## S2-16 · Test counts by commit

```
before this work                        895
+ multiplicity / batch / stats tests    939
+ cost source + reconciliation         1024
+ universe basis                       1030
+ lineup verdict                       1030
+ moex                                 1029
```

Ruff clean at every commit.

Known flake: `test_quik_fake_path_e2e` — load-dependent. Observed failing twice in the
slowest full-suite runs of the day (800 s, 600 s against a 345 s best), passing 4/4 and
1/1 in isolation on the same commit. Does not use `replay`, `phantom` or
`historical_data_service`.

## S2-17 · Commits

```
063fc87  Gate the batch for multiplicity, move estimators into the engine
6a286e8  EDGE_ROLL: the 4h autocorrelation is not the bid-ask spread
b0eb25a  The cost model is mostly fees, and a fee is not an impact
a2e4b6e  One cost, one source; and the cost model is now measured
276e9e5  Recalibrate the noise floor against measured dependence
ec8892c  Reality Check, SPA and PBO in the engine
93e3d49  Wire the batch verdict, count the universe not the batch
7aa02d9  Measure the lineup's ranking: PBO 0.933
e3bbd86  Transfer the measured clearance, fix the leak that made it urgent
f69c39f  Measure the universe: available space, not explored space
0ced7d1  Correct the universe measurement: the reader was broken
88445fa  Ask the crypto-specific question on MOEX equities
5ce0d0a  Give time_series_momentum a schema; measure the control
4e695c5  Find why schema discovery was inconsistent: an empty base class
487848b  Investigation log
2ca014a  Investigation narrative
```

## S2-18 · Error catalogue, indexed

| # | error | how it surfaced |
|---|---|---|
| 1 | `_interval_days("1d")` = 1.0 → 300 bars requested, ~206 returned | silent truncation |
| 2 | cost total reset per position open → net == gross, +120% on a zero-ρ series | known-answer check |
| 3 | research 10 bps/fill vs product 7 | discovered while decomposing costs |
| 4 | bootstrap p-value from observed mean's position in its own bootstrap | p=0.975 for mean 3.0 |
| 5 | `t < ROLL_GATE_T` with positive bar | column passed every negative reading |
| 6 | spread divided by σ again | 4175 bps = 42% |
| 7 | normalisation `var**2` vs registered `var` | contradicted the registered text |
| 8 | verdict direction inverted (`reject` read as "strategy rejected") | noise batch passed |
| 9 | trimming worst performers from a maximum | byte-identical at trim 0 and 0.3 |
| 10 | `not_evaluable` reported as `no_batch_edge` | live snapshot, 20 strategies |
| 11 | `phantom_service` writes to live gate unconditionally | inspected store before promoting |
| 12 | passing the config block returned the fallback | values identical to shipped |
| 13 | `config.get()` with no key → TypeError | service load test |
| 14 | `CONFIG_SCHEMA = {}` placeholder, discovery alphabetical | two passes disagreed |
| 15 | `resolvable_phi` used as significance test on fat tails | t=8.54 below a 0.0352 floor |
| 16 | per-fill compared with per-trade | 12.8× vs 25.6× |
| 17 | `variants_available` computed, never written to the record | **CLOSED** — see S2-14b |
| 18 | `variants_available` sized by the run's `max_variants` cap | **CLOSED** — reported 12, schema permits 36 |
| 19 | **portfolio null built by permuting the window it tests** — `_costed` depends only on `sum(legs)` and the count of non-zero legs, both permutation-invariant, so all 40 draws are one number and `w >= p90` is arithmetic | portfolio and p90 columns **identical to 4 dp in all four windows**; known-answer test with a flat −50%/bar series also scores 4/4 |

**Shared signature:** wrong in a plausible direction, plausible units, exactly where a
reader would not look twice. #19 is the purest instance of it in the catalogue: the
table looked right because `portfolio` and `p90 of null` agreeing is exactly what a
passing test looks like.

---

## S2-20 · Portfolio TSMOM on 15 ETFs — and a null that could not fail

**This section was missing.** The experiment ran, was committed (`b8b602e` registered
it, `871f2bf` recorded the result), and its numbers appeared only in a commit message
and a tool docstring. No document held them. That is catalogue error #17's class —
computed, committed, never persisted — committed by the same session that catalogued
#17. Found while writing `REGISTER.md`, which required a number no document contained.

Re-run to recover the record rather than reconstruct it from memory.

Registered: `tools/portfolio_trend.py` docstring. 252-bar signal, 252-bar vol window,
40% target vol, equal weight, the product's own 7 bps/fill read from config, 15 ETFs,
4 windows × 749 bars, 40 null seeds.

```
  window    portfolio   p90 of null
       1      -0.5955       -0.5955  Y
       2      -0.1516       -0.1516  Y
       3      -0.4292       -0.4292  Y
       4      -0.5242       -0.5242  Y

  portfolio windows at or above p90: 4/4
  portfolio total : -1.7005
  best member     : XLK -0.0479
  equal-weight of members: -1.1858

  H1 FAILS / H2 FAILS / H3 FAILS
```

All 15 members negative, from XLK −0.0479 to XLE −2.5070.

### The two columns should not have been equal

They are equal to four decimal places in **every** window. A p90 drawn from 40
permutations of a 749-bar window does not reproduce that window's total to 4 dp four
times running. The first read of this table — recorded in commit `871f2bf` and in
`narrative.md` before this section existed — was that the *criterion* was defective,
because "beats random timing" is a relative test that a losing portfolio can pass.

That diagnosis was **incomplete, and wrong about the cause**. The relative-test
objection is real and general. It is not what happened here. The null had no variance
at all:

```python
def _costed(legs):
    gross  = sum(legs)                    # invariant under permutation
    active = sum(1 for x in legs if x != 0)  # invariant under permutation
    return gross - active * COST_BPS / 1e4
```

Shuffling `legs` reorders the window without changing either term. So
`_costed(shuffled) == _costed(window)` for every permutation, all 40 draws in a window
are the same number, `p90` of a constant is that constant, and `w >= p90` is `True` for
**any** portfolio whatsoever.

### Known-answer check

`tests/test_portfolio_null_degenerate.py`, four tests, all pass:

| check | result |
|---|---|
| `_costed` invariant under permutation | exhaustive over 8 elements, 500 sampled shuffles of 700 |
| variance of the 40 null draws | exactly 0.0 |
| a flat −50%-per-bar portfolio vs its null | scores 4/4 |
| the 4/4 figure at three different seeds | 4, 4, 4 |

A criterion that passes a strategy engineered to lose is not a weak test. It is no
test. H1 could not have failed, so **H1 is NOT EVALUABLE** and the 4/4 is withdrawn.

### What stands

The portfolio total, all fifteen member totals, and H2/H3 are unaffected — none of them
touch the null. The experiment is a clean negative: TSMOM as a portfolio on these ETFs
lost money, and diversification did not rescue it.

### The null was not replaced

Swapping a null after seeing that it degenerates is the criterion-shopping registered
against in `portfolio_trend.py` and `narrative.md`. A corrected null — resampling
*which* bars are held, so the cost term can vary — is a different hypothesis and has to
be registered as one. It is not run.

The earlier finding survives in corrected form. The lesson is not "relative tests are
weak" but stronger and less comfortable: **a null that is invariant under the
transformation it applies cannot reject anything, and its agreement with the observed
value looks exactly like a pass.** That is a visibility failure of the same family as
#1, #5, #10 and #17 — right in the place nobody checks twice, because the numbers agree.

---

## S3-1 · Neuroevolution counting check — REGISTRATION (before the run)

Tool: `tools/neuroevolution_count.py`. Previous attempt died in a hand-written
backprop (`delta` re-initialised per layer); this version uses whole-matrix gradients
pinned by a finite-difference check, numpy available (2.3.5).

Space (registered): hidden (0,2,4,8) x depth (0,1,2) x activation (none,tanh,relu,
only when depth>0) x feature-subset (0,2). Canonical key collapses depth==0 to
`("logistic", fs)`; hidden==0 with depth>0 keeps its activation label (functionally
logistic — 12 of the 50 labels tie by construction). Count: 2 + 48 = **50**.
Feature-subset mapping, pre-registered: 0 = all six features, 2 = x0/x1 only
(the signal-bearing pair).

Generator: X ~ N(0,1)^6, n=2400 (train 1200 / val 600 / test 600, contiguous).
Signal: y ~ Bernoulli(sigmoid(g*(x0+x1)/sqrt(2))), g=0.5. Noise: permutation of the
signal labels -> identical marginal variance by construction.
Evolution (fixed, no tuning): pop 12, 6 generations, tournament 3, top-2 elites
carried without refit, crossover per-field, mutation 0.3. Fitness = val accuracy.
One optimiser (Adam, 500 full-batch epochs, lr 0.05) for every architecture.

Known-answer checks (run first, must pass before the main run counts):
K1 gradient check worst relative error < 1e-5; K2 noise |acc-0.5| <= 0.05,
strong signal (g=3.0) test acc >= 0.85.

Pilot (seed 101, reference logistic test acc): g=0.4 -> 0.5733; 0.5 -> 0.5817;
0.6 -> 0.6017; 0.8 -> 0.6383. Registered gain: **0.5** (weak: +8pp, val se ~2pp,
best-of-50 bias ~+4.5pp, so the val ranking is substantially luck — the R-22/R-23
configuration: real edge, noisy ranking). Registered main seed: **7**.

Criteria (fixed; H2 binding in either direction):
  H1 HOLDS iff distinct architectures < evaluations on the signal panel.
  H2 HOLDS iff (a) reference logistic (pre-specified, no selection) test >= 0.53,
       AND (b) evolution-winner val-minus-test gap >= +0.03 (selected fitness does
       not survive OOS), AND (c) evolution-winner test minus reference test <= +0.01
       (the search's pick is no better OOS than the no-selection default).
  H3 HOLDS iff PBO >= 0.7 over the evolution-evaluated distinct architectures'
       val correctness series (signal panel), vs 0.895 on the strategy lineup.
Noise panel is the control (no criterion): census val-best must look real on val
and die on test — selection on noise, displaying the bias both panels share.

## S3-2 · Neuroevolution counting check — RESULTS (seed 7, gain 0.5)

Instrument first (rule 3). K1 finite-difference worst relative error 3.917e-08
(< 1e-5). K2: noise test 0.4720 vs 0.5 (gap -0.028, within +-0.05); strong signal
(g=3.0) test 0.8640 (>= 0.85). First K2-noise read used test-majority as chance
and failed (-0.08): miscalibration of the CHECK, not the trainer — test-majority
is an unpeekable oracle; a train-fitted constant scores below it whenever the two
halves' majorities disagree. Corrected to coin-flip 0.5 before the main run.

Space: 50 architectures, 50 distinct keys (registered 50 — confirmed by
enumeration, `tools/neuroevolution_count.py::enumerate_architectures`).

```
variance check: var(signal)=0.249950 var(noise)=0.249950 base=0.5071
```

--- signal panel (test base 0.5250) ---

```
census test-acc spread over 50: mean 0.5670 sd 0.0207 min 0.5117 max 0.5867
census best-by-val: h=4|d=2|act=relu|fs=2 val 0.6083 test 0.5617
h=0,d>0 functional duplicates (12 labels): test sd 0.0033..0.0067
evolution: evaluations=62 distinct=18
evolution winner: h=8|d=1|act=none|fs=0 val 0.5817 test 0.5867 (gap -0.0050)
PBO[evolution-signal]: pbo=0.822 logits_mean=-2.861 splits_used=512 strategies=18
reference logistic (no selection): test 0.5867 (gap +0.0867 vs chance)
```

62 fits, not 72 slots: 10 top-2 elite carries needed no refit and are not
evaluations. Rule 4 counts: **62 evaluations, 18 distinct** (signal);
62 / 17 (noise).

--- noise panel, control (test base 0.5217) ---

```
census test-acc spread over 50: mean 0.5124 sd 0.0143 min 0.4467 max 0.5400
census best-by-val: h=2|d=1|act=relu|fs=2 val 0.5567 test 0.4967
evolution winner: h=8|d=1|act=tanh|fs=2 val 0.5100 test 0.4900 (gap +0.0200)
PBO[evolution-noise]: pbo=0.965 logits_mean=-3.736 splits_used=512 strategies=17
```

Verdicts against the S3-1 criteria:

- H1 (distinct < evaluations): 18 vs 62 signal, 17 vs 62 noise. **HOLDS.**
- H2a (findable): ref 0.5867 >= 0.53. HOLDS (+8.67pp, zero selection).
- H2b (selected fitness does not survive): winner gap **-0.0050**, needs >= +0.03.
  **FAILS** — the fitness survived; test lands a touch ABOVE val.
- H2c (search adds nothing over default): winner minus ref +0.0000 <= +0.01. HOLDS
  (exact tie: both linear, convex optimum is init-independent).
- H2 overall (conjunction a+b+c): **FAILS**. Evolution's pick transfers at 0.5867,
  joint-best test accuracy observed anywhere in the run (census max 0.5867, ref
  0.5867). The search is not the problem on this space/signal/budget.
- H3 (PBO high): **0.822 >= 0.7 HOLDS** (lineup figure 0.895). Noise 0.965 —
  the same number the interchangeable strategy batch printed (0.965, S2-10).

The ironic context row: census-best-by-val (the exhaustive search) overfits MORE
than evolution's winner — val 0.6083 -> test 0.5617, gap +4.7pp — while the
evolution winner gaps -0.5pp. Max-of-50 luck exceeds a concentrated 62-fit
search's luck. The mechanism the registration assumed (selection bias kills the
pick) hit the census, not the evolution.

Why the search transfers here, stated plainly: one optimiser for all 50, a smooth
weak linear signal every architecture can see, and a shared fixed val set, so
init noise per refit is tiny (duplicate-label test sd 0.0067) and the val ranking
is mostly stable signal plus one shared noise draw — not 62 independent luck
draws. Change any of those (separate tuners per architecture, needle signal,
resampled val per fit) and H2b could hold. That is a new registration, not a
reinterpretation: this outcome is binding at seed 7.

Single-seed limitation is stated, not hedged: re-running at new seeds until H2
holds would be the criterion-shopping registered against in S2-20.

---

## S4-1 · Portfolio corrected null — REGISTRATION (before the run)

R-19 stays `NOT EVALUABLE`; the 4/4 stays withdrawn. This is a NEW hypothesis
(R-36 candidate), not a repair: the old null is kept in the tool untouched, the
corrected null runs beside it. Everything else is frozen — same 15 ETFs, same
252/252/40% parameters, same 7 bps/fill, same 4 x 749 design, same windows. H2/H3
stand as FAIL and are not re-registered (recomputed for the record only).

The corrected null ("held-bars" null). Per window, let q = active/749 where
active = number of non-zero legs the portfolio actually held. A null draw holds
each of the 749 bars independently with probability q and scores
sum(held legs) - |held| * COST_BPS/1e4. Both terms vary across draws: which bars
are held moves the gross, the Binomial(|held|) moves the cost. 200 draws per
window (TRIALS), rng seed 361115, fixed. Statistic is the TOTAL net across the 4
windows; the null-total distribution is the sum over windows of independent
window draws (200 totals).

Criterion (fixed): H1 HOLDS iff observed total >= p90 of the 200 null totals.
Per-window >= p90 counts are still printed for the record (rule 9) but are not
the criterion — the portfolio is one claim, not four, and the per-window form is
what the withdrawn 4/4 wore.

Known-answer checks (synthetic windows, no network; must pass before the live
run counts — rule 3):
  N1  null spread > 0 in every window (rule 1: print the spreads first).
  N2a loser must fail: negative-drift, partially active legs (q~0.6) -> 0/4
      windows >= p90 AND total below the null median.
  N2b q=1 edge: flat -50%-per-bar legs are always active (q=1), so holding is
      forced and the null CANNOT vary — spread exactly 0. The compliant behavior
      is to refuse: 0/4 windows scored, all NOT EVALUABLE, never a pass. The old
      null printed 4/4 PASS on this exact input. Any window whose null spread is
      0 is excluded by gate, live run included.
  N3  oracle portfolio (holds the top-q bars by legs, pays the same per-bar
      cost): total >= p90. Proves the null is beatable — a test that cannot pass
      a good portfolio is no test either. If N3 fails, the run is NOT EVALUABLE,
      not a negative.
`--selftest` runs N1-N3 with no network. If Yahoo fails on the live run, that is
NOT EVALUABLE, reported not worked around (same bar as the original design).

Clarification written before any run (paper reasoning, no data seen): N2 as first
worded (flat -50%/bar must score 0/4 and below median) is unachievable as stated —
a flat series is always active (q=1), holding is forced, and ANY same-activity
timing null is a constant there. The compliant behavior is the N2b gate: spread 0
-> refuse, never pass. N2a (negative-drift partial activity) is the loser-must-fail
check. This amendment is pre-run and changes no threshold, only the named input.

Incidental (found while coding the new path, affects no recorded number): the old
path calls `_percentile(pool, 90.0)`, but the function takes a FRACTION — 90.0
clamps to 1.0, so the old "p90" is the maximum of the 40 draws. With a degenerate
null max == value and every R-19 figure stands byte-identical (reproduced below);
it would have mattered for any non-degenerate null. The new path passes 0.9.
Old path left untouched.

## S4-2 · Portfolio corrected null — RESULTS

Selftest (synthetic, seed-fixed): N1 spreads 0.51–0.81 all > 0; N2a loser 0/4,
total -9.4611 vs null median -6.2009; N2b flat -50% spreads exactly 0.0000000000
all windows -> refused; N3 oracle +18.2419 vs null p90 +9.5009. PASS, all four.

Pipeline check: `--null permuted` reproduces S2-20 byte-identical (portfolio
total -1.7005, all 15 member totals, 4/4 withdrawn shape). The instrument measures
the same object; Yahoo revisions did not move the windows.

Live run `--null held` (200 draws/window, seed 361115):

```
window 1: q=1.0000 null spread 0.000000  GATED
window 2: q=1.0000 null spread 0.000000  GATED
window 3: q=1.0000 null spread 0.000000  GATED
window 4: q=1.0000 null spread 0.000000  GATED

NOT EVALUABLE: a window's null cannot vary.
```

q=1.0000 in ALL FOUR windows: the portfolio holds a position on every one of the
2996 bars — at least one of the 15 ETFs always has a positive trailing-252d
return, so `legs` is never empty and no leg is ever zero. A same-activity timing
null for an always-on portfolio is the portfolio itself; the gate fired exactly
as N2b rehearsed. This is the instrument working (rule 1), not the question being
dodged: the S4-1 procedure's own spread check refused before any comparison was
read.

Verdict: H1 on the corrected null is **NOT EVALUABLE** (R-36 candidate) — same
vocabulary as R-19, different cause. R-19's null could not vary because the
statistic was permutation-invariant; this null cannot vary because the portfolio
is never flat. Deeper statement covering both: `_costed` sees only (window sum,
non-zero count), and no same-activity timing resample moves either one here.

What this does NOT license: designing a third null now (cross-sectional —
resampling WHICH instruments are held per bar — or turnover-costed) would be
replacing the null after seeing it gate, the exact operation S2-20 registered
against. That is a different hypothesis for a future registration. The
portfolio-corrected-null open item stays open.

---

## S6-1 · Signed order flow — REGISTRATION (before the run)

Open item: `fetch_ohlcv` returns 6 columns; Binance taker-buy fields 9–10
discarded upstream. Feasibility re-checked live before registering (not assumed):
`public_get_klines` via the installed ccxt 4.5.69 returns 12-field rows from this
network, field 9 = taker-buy base volume. No new dependency, no adapter change —
research tool, not product path. What stays untestable: the Almgren
permanent-vs-temporary split needs OUR fills; we have none. The testable question
with klines alone is prediction: does signed taker flow predict next-bar returns?

Instrument: BTCUSDT spot, 1h, ~3000 bars paged backward (1000/page, endTime walk,
rate-limited). Imbalance per bar: I = 2*takerBuyVol/baseVol - 1 in [-1,1].
Predictor: trailing-mean I over L bars; target: next-bar log return.

Checks, in order — each gates the next:
  C0  field validation ON LIVE DATA: corr(I_t, r_t) > 0.3. Flows move price
      within the bar (microstructure 101); anything else means the field
      mapping is misread and the run stops.
  K1  synthetic: returns = b*I + noise recovers |t| > 3 (instrument can see it).
  K2  synthetic: shuffled predictor gives |t| < 2 (instrument does not invent it).
      Plus the spread gate: constant predictor -> NOT EVALUABLE, never scored.
  H   (R-38 candidate) trailing imbalance predicts: |t| > 3.00 for L in {1,4}
      (two lags, both reported; bar 3.00 is the R-13 Bonferroni bar and stays
      stricter than 2-lag Bonferroni 2.4 needs). HOLDS iff either clears.
t = r*sqrt(n-2)/sqrt(1-r^2) on the correlation. One instrument, one timeframe,
two lags — the denominator is on the page.

## S6-2 · Signed order flow — RESULTS

Selftest (no network): K1 t=+63.44, K2 t=+1.60, constant predictor refused. PASS.

Live run (3000 BTCUSDT 1h klines, 2999 aligned bars):

```
C0 contemporaneous corr(I, r) = +0.4086 (need > +0.30) -> fields real
lag     n      corr        t
  1  2998   -0.0235    -1.29
  4  2995   -0.0213    -1.16

H (criterion |t| > 3.00): FAILS
```

Both lags negative, neither near the bar — directionally a whisper of the 4h
reversal family, statistically nothing. Verdict: **H FAILS with evidence
(R-38)**. Signed taker flow is now obtainable (12-field raw klines from this
network, tool kept) and it does not predict next-hour BTC returns at these
lags. The open item is CLOSED: the endpoint exists, the question it blocked is
answered negatively.

---

## S7-1 · Neuroevolution replication — REGISTRATION (before the run)

R-34 (seed 7) falsified H2b: the evolution winner's fitness survived
(val-test gap -0.005). Single seed was stated as a limitation. This is the
replication, registered as its own hypothesis (R-39 candidate) — NOT a re-run
to overturn: the design is frozen bit-for-bit (gain 0.5, pop 12, 6 gens, same
code, same thresholds), only the seeds are fresh (8, 9, 10, 11, 12 — none used
by pilot/known/main).

Criterion (fixed): R-39 HOLDS iff on >= 4 of 5 seeds the winner's val-test gap
is < +0.03 (the H2b bar it failed to reach at seed 7). Sanity, not criterion:
reference-logistic test >= 0.53 every seed (signal present); reported alongside
census-best test and winner-minus-census gaps as context. If the signal itself
is absent on a seed, that seed is reported, not dropped.

## S7-2 · Neuroevolution replication — RESULTS (seeds 8-12, gain 0.5)

Signal panel, per seed (ref test / winner val->test = gap / census-best test):

```
seed  8: ref 0.5950   winner 0.6183 -> 0.5933 = +0.0250   census-best 0.6000
seed  9: ref 0.6000   winner 0.5733 -> 0.6000 = -0.0267   census-best 0.5717
seed 10: ref 0.6050   winner 0.6150 -> 0.6000 = +0.0150   census-best 0.6000
seed 11: ref 0.5667   winner 0.6033 -> 0.5700 = +0.0333   census-best 0.5717
seed 12: ref 0.6217   winner 0.5783 -> 0.6217 = -0.0433   census-best 0.5867
```

Gap < +0.03 on 4/5 seeds (8, 9, 10, 12). Seed 11 misses by 0.33pp — and its
winner test (0.5700) essentially ties census-best (0.5717), so the miss is bar
arithmetic, not a transfer failure of substance. Reference >= 0.53 on all five
(+6.7 to +12.2pp: signal present everywhere). **R-39 HOLDS.**

Context the criterion did not require but the record should keep:
- H1 re-holds on all five (distinct 18–26 vs 62 evaluations, both panels).
- H3 re-holds on all five: PBO signal 0.934 / 0.709 / 1.000 / 0.908 / 0.979
  (mean 0.906 against the lineup's 0.895 — the ranking is noise on every seed
  while the winners transfer).
- H2c re-holds on all five (winner minus ref: -0.0017 / +0.0000 / -0.0050 /
  +0.0033 / +0.0000).
- Twice the search beats or ties exhaustive selection OOS: seed 9 winner 0.6000
  vs census-best 0.5717; seed 12 winner 0.6217 = census max 0.6217.
- Noise panels stay null-like throughout (census test means 0.489–0.513).
- Seed 8's winner is h=0|d=2|act=none|fs=2 — a functionally-logistic label —
  consistent with a linear signal: capacity beyond linear buys nothing here.

R-34 stands replicated: on this space/signal/budget the evolution pick
transfers. The ML lead is NOT closed negatively — and that is now a 6-seed
statement, not a single-seed one. What could still break the search (separate
tuners per architecture, needle signal, resampled val per fit) remains a
different registration.

---

## S8-1 · Ensemble, the usable form — REGISTRATION (before the run)

Unfinished business, constructive direction (breaking the search is declined):
S3/S7 show the winner transfers AND the ranking is noise (PBO 0.7–1.0 every
seed) — the R-22/R-23 configuration, whose moral was "don't pick, average."
This registers the ML version: averaging the evaluated population transfers,
and picking the winner adds nothing over it.

Design frozen (gain 0.5, space, optimiser, evolution; fresh seeds 13–17, none
used before). Ensemble = mean predicted probability over the DISTINCT
architectures evolution evaluated, thresholded at 0.5 (census-50 ensemble as
context only). Code change is additive: average stored models' test
probabilities after the run; no existing number moves.

Criteria (fixed): R-40 HOLDS iff on >= 4 of 5 seeds (a) ensemble test >= 0.53
(transfers, the H2a bar) AND (b) |ensemble test − winner test| <= 0.02
(selection adds nothing over averaging). Binding both ways: if the winner
systematically beats the average, selection DOES add value — also interesting,
and also recorded.

## S8-2 · Ensemble — RESULTS (seeds 13-17, gain 0.5)

Signal panel, per seed (ensemble test / winner test / gap):

```
seed 13: ensemble 0.5983   winner 0.5917   +0.0067   (census-50 ens 0.5933)
seed 14: ensemble 0.5833   winner 0.5883   -0.0050   (census-50 ens 0.5783)
seed 15: ensemble 0.5917   winner 0.6017   -0.0100   (census-50 ens 0.5883)
seed 16: ensemble 0.5717   winner 0.5717   +0.0000   (census-50 ens 0.5717)
seed 17: ensemble 0.5883   winner 0.5917   -0.0033   (census-50 ens 0.5950)
```

(a) holds 5/5, (b) holds 5/5 — max |gap| 1.0pp. **R-40 HOLDS.** Averaging the
evaluated population transfers (+7 to +10pp over chance); picking the winner
adds nothing over it in either direction.

Honest outlier, same seeds: seed 16 is the one case where the winner's val WAS
lucky (0.6300 -> test 0.5717, gap +0.0583 — H2b would have HELD there). Yet the
test still lands at 0.5717 and the ensemble matches it exactly. Selection luck
moves val, not the OOS answer — which is the same moral from the other side.

The ML arc now reads whole: the search transfers (R-34, replicated R-39), the
ranking is noise (R-35, PBO high every one of 11 seeds), and the usable form is
the average, not the pick (R-40, 5/5). Nothing in it trades on real data; that
boundary is stated, not crossed.

---

## S9-1 · ML on real data — REGISTRATION (before the run)

The boundary crossed deliberately, as its own hypothesis (R-41 candidate) —
not as a plugin. Same space (50), same optimiser, same evolution (pop 12,
6 gens, val-accuracy fitness), same ensemble form (mean prob over distinct
evaluated). What changes is ONLY the data: synthetic Gaussian features become
6 real ones — BTCUSDT 1h log returns [r_t..r_{t-5}] predicting 1[r_{t+1}>0],
closes from the S6 raw-klines endpoint. 6000 bars contiguous: train 3600 /
val 1200 / test 1200. Standardize on train only. Single seed of data (the
market has one history); the seed that varies is the optimiser/evolution seed,
fixed at 41.

Costs inside the criterion (S2 rule: no result without costs): position long if
P>0.5 else flat, flip = 7 bps. Net = Σ pos·r − flips·7e-4 on TEST.

Criterion (fixed): R-41 HOLDS iff (a) ensemble test accuracy ≥ 0.53 (the H2a
bar, transfers) AND (b) ensemble test net > 0 (profitable after costs).
Accuracy without profitability is not a trading result. Reported alongside
(winner, reference logistic, census-best, PBO over evaluated val series, both
counts) as context. Prior stated openly: 0/40 tradable edges; the likely
outcome is FAIL, most plausibly at (b) — R-11's 4h edge was 25x under costs.

Leakage pin (unit test, no network): builder on hand-computed prices must
return the exact lag matrix — features strictly past, target strictly future.

---

## S10-1 · Fee schedule audit — REGISTRATION (before the run)

Last open item: reconciliation runs hourly but never saw a real fill. This
closes it by AUDIT, not assumption: census every journal row (`data/ops/
trades.jsonl`), classify fill-like rows against DOCUMENTED test markers, and
count rows usable as `ExecutionObservation`s (needs reference_price — decision
price; rows without one are excluded, not zero-filled, per the shipped rule).

Documented markers (all in-repo before this run): strategy 'a' + 100.0 (unit
tests); price 159.0 (test-only price, conftest); SBER 285.0 = FakeQuikTransport
base price (`test_quik_path_e2e.py:72`, `test_quik_transport.py:35`; codebase's
own tests assert real rows differ from it); reason 'paper' (simulated, not a
venue fill); `orders.json` fills object (checked empty/non-empty as listed).

Criterion (fixed): R-42 HOLDS iff >= 30 real fills WITH reference prices yield
a calibration report with sufficient:true (`calibrate` minimum_samples=30).
Anything less — including the lenient 10-fill "a number, not a trustworthy
one" bar — is NOT EVALUABLE, with the unblock condition stated: first real
fill with a decision price. Tool: `tools/fill_audit.py`; rules pinned on
synthetic rows (no production data in tests).

## S9-2 · ML on real data — RESULTS (BTCUSDT 1h, 6000 bars, seed 41)

Leakage pin passes (exact lag matrix; net test caught my own one-fill
undercount — exit costs too, code was right). Live run, test = last 1193 bars:

```
census test-acc spread over 50: mean 0.5055 sd 0.0105 min 0.4686 max 0.5331
census best-by-val: h=4|d=2|act=tanh|fs=2 val 0.5383 test 0.4887
evolution: evaluations=62 distinct=22
evolution winner: h=2|d=1|act=tanh|fs=0 val 0.5208 test 0.5189
PBO signal 0.986 / noise 0.992
reference logistic: test 0.5013
ensemble: test 0.5264 (R-41a needs >= 0.53)
TEST NET long/flat 7bps/flip: ensemble -0.0858  winner -0.1677
    reference -0.1424  (R-41b needs ensemble > 0)
buy-and-hold test window +0.0538
```

(a) 0.5264 < 0.53 — misses by 0.36pp, and that margin is reported, not rounded
into a hold. (b) −0.0858, negative. **R-41 FAILS on both bars.** The census
spread sits on chance (mean 0.5055, sd 0.0105 — the 0.5331 max is best-of-50
luck); the val-selected census-best tests BELOW chance (0.4887); the ranking is
noise (PBO 0.986); every net is red while buy-and-hold prints +0.0538.

This is the answer the boundary question owed: the machinery that transfers on
a present signal finds nothing to transfer here — because, 40 hypotheses later,
there is still no demonstrated edge for it to find. The ML lead is now closed
in BOTH directions with evidence: negatively on real data (R-41), positively on
synthetic (R-34/R-39/R-40). Neither direction licenses a plugin: the first says
it does not work where it would have to, the second says only where the answer
was planted.

---

## S5-1 · Portfolio cross-sectional null — REGISTRATION (before the run)

Second attempt at the open item, registered as its own hypothesis (R-37
candidate). S4 showed same-activity TIMING resamples cannot move (sum, count)
for an always-on portfolio. This null resamples the CROSS-SECTION instead: per
bar, the observed rule holds k_i instruments (k_i = #{sig>0}, live always ≥1);
a null draw holds a uniform random k_i-subset of the 15, weighted by the same
inverse-vol formula from the same trailing vols, normalized the same way, costed
the same per held bar. Invested-ness fixed bar-by-bar; only WHICH varies. Spread
comes from the cross-section, and the S4 gate stays: any window with zero null
spread is excluded, never scored.

Shared code path: observed and null both score subsets through one function —
the observed pick is one particular subset, the null draws random ones. Cost
comparability is structural, not asserted.

Frozen: instruments, 252/252/40%, 7 bps/fill, 4 x 749 windows, same bars.
H2/H3 stand as FAIL (recomputed for the record only). Statistic is again the
TOTAL net across 4 windows vs 200 null totals (seed 362115). Criterion (fixed):
H1 HOLDS iff observed total >= p90 of the null totals. Per-window counts printed
for the record only — one claim, not four.

Known answers (synthetic 6-instrument panels, no network, `--selftest2`):
  C1  spread > 0 on ordinary windows.
  C2a anti-selection (holds worst-half by trailing-20d mean, all drifts
      negative) -> 0/4 windows >= p90 AND total below null median.
  C2b full-hold (k = all every bar) -> spread exactly 0 -> refused, never scored.
  C3  pro-selection (holds 2 best trailing-20d in a panel where 2 of 6 drift
      +0.015 vs noise 0.02, rest -0.005) -> total >= p90. Beatable null or no test.
If C3 fails: NOT EVALUABLE, not a negative. Small bars/draws in-test (200/50);
live design unchanged (749/200).

Calibration note (pre-live-run, synthetic only — the registered criterion, live
design and seeds are untouched): the first C2a synthetic used EQUAL drifts
(-0.003) on all six instruments, under which any same-size subset has the same
expectation and the check is vacuous (observed 1/4, total -3.0688 vs median
-3.1208 — above it). Replaced with split drifts (-0.008 x3, 0.0 x3) so
anti-selection is genuinely harmful: 0/4, -5.8069 vs -3.9220. C3 margin:
+11.1720 vs p90 +1.2753. This tunes the SYNTHETIC loser, which exists only to
prove the null ranks sensibly — it cannot move the live verdict in either
direction.

## S5-2 · Portfolio cross-sectional null — RESULTS

Selftest2: C1 spreads 0.41–0.60; C2a 0/4 below median; C2b full-hold spreads
exactly 0 -> refused; C3 clears. PASS, all four. Live reproduction guard held:
shared scorer on observed picks == registered port_net in all 4 windows
(assert, < 1e-9) — the panel refactor did not move the measured object.

Live run `--null cross` (200 draws/window, seed 362115):

```
window 1: mean k=10.04/15 null spread 0.844685
window 2: mean k=11.59/15 null spread 0.160913
window 3: mean k=10.03/15 null spread 0.407992
window 4: mean k=9.83/15 null spread 0.445615

window    portfolio   p90 of null
     1      -0.5955       -0.0805  .
     2      -0.1516       -0.1647  Y
     3      -0.4292       -0.2837  .
     4      -0.5242       -0.2406  .

windows >= p90: 1/4 (record only)
null-total spread: 1.172987
portfolio total : -1.7005
null median     : -1.1904
null p90        : -0.9895

H1 (criterion: total >= p90): FAILS
```

Every window's null varies (no gating), the totals null spans 1.17, and the
portfolio total sits BELOW the null median: random same-size instrument subsets
would have lost less (-1.19) than the TSMOM selection (-1.70). H2/H3 recomputed
identical (both FAIL, stand). Verdict: **H1 FAILS with evidence (R-37)** — the
first evaluable answer the portfolio-timing question has ever received, and it
is negative. The portfolio-corrected-null open item is now CLOSED: same-activity
timing has no variance to test (R-36), and cross-sectional selection loses to
random (R-37).

---

## S11 · Gate hardening — engineering record (no new hypothesis)

Re-derived from code after the whole-project audit, not from the register. One
premise produced four defects: *an uncomputable statistic may be replaced by a
neutral default*. At the gate the neutral default was a pass.

```
research_digest.py   p_value missing -> 0.0 (most significant possible value)
research_digest.py   ci missing      -> ci_crosses_zero=False (clean interval)
research_gate.py     pbo.ok=False    -> "not unstable" -> batch_edge_stable
research_gate.py     max_pbo from YAML never passed into the verdict
```

Red-first tests in `tests/test_gate_fail_closed.py` — all four failed for the
expected reason, then passed after the fix:
missing p stays None and is blocked by `apply_multiplicity`; missing interval
reads as crossing zero; an unmeasurable PBO yields `not_evaluable`; `max_pbo`
is honoured. `evaluate_many` now threads `pol.max_pbo` into the batch verdict.

Second defect: **the decision path did not call the batch gate at all.**
`_enforce_research_gate` called `evaluate_live_readiness` per id — a function
that cannot see the set, so Holm/RC/SPA/PBO never applied to what trades.
`SET_ENSEMBLE` used `live_promotion_blockers`, the same limitation.

```
disk fixture: 8 interchangeable strategies, 40 folds each
evaluate_live_readiness per id  -> live_ok True, no blockers
evaluate_many (batch)           -> PBO 0.965, verdict unstable_selection, live_ok []
before fix: startup kept all 8; after fix: demotes all 8 with batch_unstable_selection
```

Both paths now call `evaluate_many`. An N=1 lineup has no selection to correct,
so the batch family is `not_applicable` there rather than blocking the shipped
single-strategy config (pinned by a fixture that clears per-strategy).

Also de-timebombed three test fixtures whose hardcoded `data_end: 2026-10-01`
had become 54 days stale against `max_age_days: 30`, making `live_ok`
assertions fail for a reason unrelated to the logic under test.

Verification: full suite **1062 passed**, ruff clean, four red-first gate tests
plus two decision-path tests. Commits `ee42315`, `9c306c1`.

What this does NOT do: it cannot create an edge. It makes the measurement and
the enforcement honest — after these fixes a strategy can only trade if the
whole batch family clears, and the batch family still says the ranking is noise.

---

## S12 · Standing verification loop — synthetic + historical

`tools/run_verification.py` is now the single feedback command. Offline:
12 pytest modules (synthetic / on-disk fixtures) + the three known-answer
self-tests. `--live` adds fresh BTC 1h klines, the real-data ML run, order flow
and the fill journal. A non-zero exit from `fill_audit` is registered as an
EXPECTED FINDING (`NOT EVALUABLE`), not a harness failure.

Latest live re-derivation (new kline window, so numbers differ slightly from
S9-2/S6-2; conclusions unchanged):

```
BTCUSDT 1h, 6000 klines, 5993 rows (train 3600 / val 1200 / test 1193)
census mean 0.5111 sd 0.0122   PBO signal 0.998 / noise 1.000
ensemble test 0.5130 (< 0.53)   net -0.3906  (R-41 still FAILS both bars)
order flow C0 +0.3647 (fields real); lags t=-0.61 / +0.35 (H still FAILS)
fill audit: 0 usable observations out of 9718 journal rows
```

Tooling fixes in this increment: `apply_canary_quantity` now writes back to
`plugins.<sid>` (the location it reads from) instead of `raw_cfg[sid]` — the
canary was a no-op. `adjust_pvalues(..., n_tests=)` and
`apply_multiplicity(..., universe=)` add Sullivan's available-space correction
as an explicit capability; a red-first test showed the Holm/BH loops indexed
unobserved p-values and crashed — fixed by ordering only observed values while
scaling by the full family. **The live gate's default correction is unchanged**:
switching it to the universe is a separate, pre-registered decision because it
would move every verdict.

Verification: offline 112 passed + 3 self-tests; `--live` PASS; full suite
**1067 passed**; ruff clean.