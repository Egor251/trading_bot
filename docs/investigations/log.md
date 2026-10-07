# Investigation log: how this system concluded nothing was there

Not a paper. Not a conclusion. A record of an attempt to find an edge in crypto
markets that failed, kept in the order it failed, including the parts where the
instrument was wrong.

**Status: open.** Nothing here is final. The last section lists what was still broken
when the log was started.

## Provenance, and one gap in it

This file is assembled from two sources, and they are not equally complete:

| period | source | fidelity |
|---|---|---|
| Session 1 (searches, mechanism tests) | a summary of that conversation, carried forward as working context | **second-hand** — decisions and numbers, not verbatim exchanges |
| Session 2 (corrections wired, lineage) | direct: runbook, git history, tests | verbatim in substance |

Session 1 was not transcribed while it happened. What survives is its summary: the
registered criteria, the measured numbers, the refutations and the corrections — but
not the intermediate reasoning, the dead ends, or the wording of anything.

**If the verbatim first session is wanted, it has to be pasted in.** It cannot be
reconstructed from here without inventing it, and inventing it would defeat the
purpose of the document.

The claim this log makes is not "these are all the errors". It is "these are the
errors that were caught". That asymmetry is the whole subject.

---

## 1. The claim being tested

Does any of these strategies have positive expectancy after costs, out of sample,
after paying for how many were tried?

Everything else is a rule about how to answer it.

## 2. The floor: what "nothing found" is worth

The market was measured before the strategies were.

**Predictability ceiling.** Autocorrelation estimated with Newey-West, variance ratio
by Lo–MacKinlay, pooled and split across windows. Result: no edge above the detection
threshold of φ = 0.05 anywhere. The negative results therefore have two candidate
explanations, and for a long time only one was tested: *the strategies found nothing*.

**Power, measured rather than assumed.** Before drawing conclusions from nulls, the
null's *power* was measured — inject an edge of known size, see how often it is
detected. φ = 0.05 lifts detection by 6.5 pp above the noise floor. So negatives at
this sample size are informative, not merely inconclusive.

**The noise floor was wrong, and in the flattering direction.** The null was i.i.d.
gaussian — autocorrelation exactly zero — while the real market has φ = −0.044 at 4h
and −0.049 at 1d. Injecting the measured value into the null, the market factor and
the strategies together, holding marginal variance fixed:

```
null phi     rate      se    vs base
  +0.000    19.0%   0.30      +0.0 pp
  -0.044    21.8%   0.32      +2.8 pp
  -0.049    21.8%   0.32      +2.8 pp
  -0.068    21.8%   0.32      +2.9 pp
  +0.050    21.8%   0.32      +2.9 pp   positive control
```

**Supported** at roughly nine standard errors: the floor was 2.8 pp too low. The
false-positive rate is **21.8%, not 19.5%**, and the error ran in the direction that
flatters a strategy.

Two things the controls settled:

- **The sign does not matter, the dependence does.** +0.050 moves the floor as much as
  −0.044, and all three negative values are indistinguishable. The recalibration needs
  only that small dependence exists, not the precise measured value.
- **The detection floor does not move.** Re-running the power ladder against the
  measured null, φ = 0.05 still clears the registered +5 pp margin (+7.2 pp). That is
  structural: the margin is a difference between two rates measured under the same
  dependence, so the base rises while the sensitivity barely moves. **All six
  registered searches keep their verdicts.**

## 3. The searches, all negative

Pre-registered each time, criterion fixed before data was seen, multiplicity
corrected on every grid.

| search | tested | outcome |
|---|---|---|
| ceiling | predictability of the market itself | negative — nothing above φ = 0.05 |
| 10-window design | 28 → 210 raw, 30 demeaned | zero at 10/10 |
| asset-class sweep | 12 instruments | 0/12 positive, 9 negative |
| momentum | 24 807 S&P bars back to 1927, 7 horizons | none clears; φ = −0.0127 |
| indicator panel | 56 tests | 0 clear |
| breakout (from an Elliott paper) | 6 variants | 0/6 |

**EWO / MA(5)-vs-MA(34)**, extracted from an RBC explainer as a *rule* rather than its
conclusions: +334% BTC / +406% ETH daily, survived a down-market test including +88%
through the Great Depression with the S&P at −77.5%. **Failed** confirmation on fresh
instruments (3/5, none significant), and the indicator panel reproduced it as its
`ema-50` row — meaning it was long exposure, not a signal.

**The real finding of session 1:** negative 4h first-order autocorrelation,
t = **−3.44** (BTC, 20 004 bars), −3.01 (ETH). Realized edge **0.78 bps/trade against
a ~20 bps round trip — a 25× shortfall.**

## 4. The 4h autocorrelation: real, and not tradable

The bounce test held (2.61× on DOGE) but the *sign ordering* ran opposite to bounce,
which pointed at liquidations and funding rather than spread.

**A correction was issued to my own earlier statement.** The effect was described as
worth 15 bps/trade — "a pricing problem". The breakeven curve showed 0.6 bps per
fill. A 25× shortfall, not 1.3×. Stated explicitly rather than quietly amended.

## 5. The cost model is mostly fees

The research sweeps charged **10 bps per fill**; the product's own config charges
`fee_bps: 5` + `slippage_bps: 2` = **7**. Two cost numbers in one repository —
research running 1.43× more pessimistic than the system it documents. **The 14th
recorded instance of a quantity recorded in one place and interpreted in another.**

Decomposing it, against a spread measured at 1.19 bps:

```
total charged      7.00 bps/fill
fee (a transfer)   5.00 bps   71.4%
measured crossing  1.19 bps
residual slippage  0.81 bps
impact total       2.00 bps   28.6%
```

A fee is a transfer to the venue. It does not scale with size, does not decay when the
order completes, and no execution schedule touches it. Describing it as permanent
impact overstates how much of the barrier is addressable; describing it as temporary
implies a scheduling fix that does not exist.

**The unit trap, which halved the barrier.** An edge is naturally per *trade*; a cost
schedule is quoted per *fill*. Compared directly, 20/0.78 reported as 12.8× where the
round-trip comparison is 25.6×. The 25.6× was right; the fee share was 83% of it.

**The conclusion survives the correction.** At a zero fee the measured spread alone is
still 1.5× the edge. What changes is that the barrier is about half what was quoted —
and a strategy cannot close that gap by executing better.

## 6. Roll (1984): validating the instrument, then breaking the hypothesis

Rule taken from the paper: in an efficient market a negative first-order serial
covariance is a *measurement* of the spread, recoverable from prices alone.

**All four registered criteria failed.** ρ₁ = −0.0436 at 4h, −0.0494 at 1d — the
*longer* horizon is *more* negative, the opposite of the bounce prediction. Implied
spreads of 60–157 bps, credible on no exchange.

Three bugs in the instrumentation, surfaced by a first run reporting **4175 bps** —
that is 42%, and it is how they were found:

- **Units.** γ₁ built from log returns, so its square root is already return-scale.
  Dividing by σ again inflated everything by 1/σ ≈ 200.
- **Sign.** `t < ROLL_GATE_T` with a positive bar and a negative t is always true. The
  G1 column passed every negative reading; 3/4 reported where 1/4 was correct.
- **Normalisation.** Code used `gamma1/var**2` against a registered `gamma1/var`.
  Corrected to the registered form and left there — moving it back after seeing which
  direction helps would be criterion-shopping.

**Walking the frequency settled it, and validated the instrument on the way:**

| bars | ρ₁ | implied spread |
|---|---|---|
| 1m | −0.0239 | **1.19 bps** |
| 5m | −0.0254 | 3.49 bps |
| 4h | −0.0515 | 35.47 bps |
| 1d | −0.0473 | 103.82 bps |

At 1m the estimator returns a believable BTC spread, stays sane at 5m, then inflates
without limit. **The bounce is real, small, and lives where the estimator is valid. The
4h/1d effect is not it.**

A test on the estimator itself recorded a subtlety: an i.i.d. ±amplitude series has
γ₁ **exactly zero** — negative covariance requires the bid-ask side to *alternate*,
because consecutive independent signs have no covariance however large the amplitude.

## 7. Is it crypto-specific? Asked on MOEX

```
SBER  n=3402  σ 2.13%  ρ1 +0.0046  t  +0.24
GAZP  n=3099  σ 2.12%  ρ1 +0.0142  t  +0.28
LKOH  n=3402  σ 1.89%  ρ1 -0.0089  t  -0.31
GMKN  n=3095  σ 8.48%  ρ1 -0.0149  t  -8.54
ROSN  n=3099  σ 2.09%  ρ1 +0.0567  t  +1.72
```

Four liquid MOEX blue chips show nothing. The exception is GMKN — a stock that fell
from 7029 to 118, on 8.5% daily sigma.

**This is not "markets are negatively autocorrelated".** It is what EDGE_ROLL already
implied for crypto: the negative covariance is **bounce after a sharp move**, and it
scales with how violent the series is. BTC at 4h: modest sigma, t = −3.4. GMKN:
8.5% sigma, t = −8.5. Same mechanism, magnitude tracking the violence, now with an
independent venue supporting it.

**So the crypto searches do not transfer to equities as a general claim.** The
predictability ceiling has to be argued per venue, and per regime within a venue.

**The criterion was wrong first, and the data said so.** The first run reported 0 of 5
— no reproduction at all — and was wrong on GMKN: |ρ₁| = 0.0149 sat *below* the
`resolvable_phi` floor of 0.0352 while |t| = 8.54, which cannot both hold. A |t| of 8.5
implies a standard error near 0.0017, ten times smaller than 1/√n. `resolvable_phi`
assumes the AR(1) standard error behaves like `1/sqrt(n)`; that holds for
homoskedastic data and not for fat-tailed equity returns, and using it as the
significance test **discards precisely the series the direct t finds most significant.**

## 8. The corrections, and three of them were dead code

Found in the codebase, correct, tested, and called from nothing:

| function | status found |
|---|---|
| `adjust_pvalues` (Holm/BH) | implemented, tested, **zero callers** |
| `variants_tried_lifetime` | stored, commented "the denominator a deflation argument needs", **never read** |
| `calibrate` / `nightly_fee_reconciliation` | complete, **run only in tests** |
| `prometheus exporter` | existed, nothing imported it — ten alerts could never fire |

Wired all four. Same defect each time: correct code, no caller.

## 9. The universe, measured

Sullivan, Timmermann & White (1999), in one line: correct over the space the rules were
drawn from, including the ones never reported.

Eight strategies each drawn from a grid of twenty count as a universe of **160**, not
8. And the available configuration space is larger than the explored one:

```
full available space, discoverable schemas   488
never searched                                428  = 88%
```

Walk-forward evaluates **3** combinations per strategy against 16 or 36 the schema
permits. The reported configuration is a maximum over a search whose reach was
arbitrary — the same pipeline with `max_variants=36` returns a different winner.

## 10. Ranking is noise — on the product's own strategies

`tools/lineup_verdict.py`, 22 strategies × 3 tickers, walk-forward, all four
corrections applied to the batch. A batch passes only if it survives the whole family.

```
universe 247
Reality Check  p = 0.0020   reject      a detectable batch effect
SPA            p = 0.0020   18 after reliability trimming
PBO            0.895        495 splits  worse_than_random
verdict        unstable_selection
```

**PBO = 0.895.** The in-sample winner finished out-of-sample below where it started in
89.5% of re-splits; a coin toss is 50%. The ordering is close to actively misleading.

The stronger evidence is that two runs disagree. Five folds, then twelve, nothing else
changed:

| strategy | 5 folds | 12 folds |
|---|---|---|
| momentum_roc | −28.33 | **+61.06** |
| volume_impulse_example | −110.91 (p=0.001) | −48.94 (p=0.429) |
| turtle_breakout | +74.12 (p=0.050) | +100.80 (p=0.005) |
| best strategy | turtle_breakout | **dual_thrust** |

Same strategies, same data, one parameter different, and the winner changes. **PBO
made visible without any statistics.**

The finding is not "nothing cleared" — that was already known. It is that the batch
**simultaneously** shows a detectable aggregate effect *and* an unusable ranking, and
those are different failures with different remedies: aggregate-only strategies might
still be worth trading as a portfolio, while anything driven by the ranking — best
goes live, best gets capital, top-1 promoted — is trading a coin flip.

**Folds went 5 → 12 for measurement resolution and nothing else was touched.** CSCV
needs as many observations as it splits into; at 5 folds it returned
`insufficient_series` and H3 could not run at all. Widening a window until a test can
run is different from widening it until it passes.

## 11. The control

`random_baseline` has no edge by construction (`trade_probability: 0.08`). It is now in
the lineup, and it is **blocked on six checks**: `ci_excludes_zero`, `profit_factor`,
`mean_oos_pnl`, `tickers_robust`, `multiplicity`, `batch_unstable_selection`.

It returned a positive out-of-sample mean of +15.38 at p = 0.153. That is not a
failure — it is the noise floor behaving as the null calibration says it should, with
six independent checks catching it. **If the control ever clears, every number in this
log is void.** That check now runs on every regeneration.

---

## Appendix: the error catalogue

The recurring pattern, in the order it was named. This is the most transferable thing
here.

1. `yahoo_adapter._interval_days("1d")` returned `1.0`, so 300 daily bars were
   requested and ~206 returned silently.
2. A cost total reset on every position open, so `net == gross` — a rule reported
   **+120% on a zero-autocorrelation series**. Caught by a known-answer series.
3. Cost: research 10 bps/fill against product 7 bps/fill.
4–10. Variants of the same class: a quantity recorded in one place and interpreted in
   another, in the exposure metric, the data path, the variant denominator, the
   lifetime counter, the universe.
11. A bootstrap p-value computed as "where does the observed mean fall inside its own
    bootstrap distribution" — which is ≈ 1 for every input, because a bootstrap
    distribution is centred on the observed statistic by construction. It reported
    **p = 0.975 for a mean of 3.0 with CI [2.93, 3.09]**.
12. Trimming the *worst performers* from a **maximum** — arithmetically a no-op.
    Implemented first, byte-identical at trim 0 and 0.3, and it looked like a safeguard.
13. **`phantom_service.run_walk_forward` wrote to the live clearance store
    unconditionally**, via an absolute path inside the repository, wrapped in
    `except: pass`, with no switch. A research tool that documented "writes to its own
    store, never the live one" wrote 20 entries to the live store and inflated
    `variants_tried_lifetime` from 3 to 12 — **the Sullivan denominator, polluted with
    the experiment's own repetitions.**
14. `StrategyPlugin` declares `CONFIG_SCHEMA = {}` as a placeholder. Discovery took the
    *first* class with a dict schema and `dir()` is alphabetical — so it stopped on the
    base class and never reached `SupertrendFollowPlugin`, which sorts later. **Which
    strategies were missed depended on alphabetical position.** In
    `phantom_service` the empty dict was accepted as real, making
    `variants_available == 1` for every strategy.
15. `not_evaluable` reported as `no_batch_edge` — all 20 strategies looked like
    refutations when no test had run at all.

**The distinguishing feature of every one of them: it was wrong in a plausible
direction, with plausible units, right where a reader would not look twice.**

## What this log does not claim

- Not that markets are unpredictable. The ceiling argument is per venue and per regime.
- Not that the 4h autocorrelation is exploitable. It survives as a measured effect and
  fails as a trade, at a barrier roughly half the one originally quoted.
- Not that the strategies are worthless. Nothing here is evidence about any individual
  strategy's forward expectancy.
- Not that 88% of the search space is worthless. Only that it was never searched.
- Not that these are all the errors. Only that these are the ones that were caught.

## Open when the log was started

1. **`variants_available` does not reach the record.** Computed in `phantom_service`,
   absent from the provenance the digest sees, `variants_available_lifetime` carrying
   the *explored* count instead. Narrowed to two candidates — a second provenance block
   on another path, or an allow-list in `digest_walk_forward` / `record_digest_clearance`
   — and **not resolved**. Until it lands, `universe_basis: available` is a setting with
   no effect on the number.
2. **Signed order flow is unobtainable.** `ccxt.fetch_ohlcv` returns six columns;
   Binance's taker-buy volume (raw kline fields 9–10) is discarded. The permanent-vs-
   temporary impact split is untestable without an exchange-specific raw endpoint.
3. **The fee schedule is assumed, not measured.** The reconciliation now runs on a
   schedule and needs real fills to produce anything.
4. **Session 1 is second-hand here.** See provenance.