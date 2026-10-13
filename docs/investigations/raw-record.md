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