# Register: every hypothesis registered, with its verdict

The single place that answers *what was tested, what was the criterion, and what
happened*. Built from `raw-record.md`, the runbook, and the parallel session's
catalogue. Nothing new is measured here — this is the index those documents lack.

**Verdict vocabulary**, fixed so rows mean the same thing:

| verdict | meaning |
|---|---|
| `HOLDS` | criterion met, and the criterion itself survives review |
| `HOLDS / CRITERION DEFECTIVE` | criterion met, **but the criterion would have passed on a losing result** |
| `FAILS` | criterion not met |
| `NOT EVALUABLE` | the test could not run; reported rather than worked around |
| `ANSWER` | the experiment's purpose was a number, not a verdict |
| `CLOSED` | a defect, not a hypothesis; now fixed and regression-pinned |

The `CRITERION DEFECTIVE` value is the one that matters and it was added because it
occurred. See R-19.

---

## Session 1 — the searches

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-01 | market is predictable enough to trade | positive φ clearing detection floor | Newey-West AR(1), pooled + per window | crypto multi-horizon | `FAILS` |
| R-02 | the method can see a real edge | smallest injectable φ detected > 5 pp above floor | power sweep | synthetic | `ANSWER` φ = 0.05, +6.5 pp |
| R-03 | p90 is calibrated at 10% | measured rate vs nominal | 200×21×4 trials | synthetic | `FAILS` — 12.3 / **19.5** / 22.0% |
| R-04 | ≥10 windows permits any claim at all | 2^−W < 0.05/S | exact arithmetic | — | `ANSWER` ≥10 windows required |
| R-05 | 10-window design clears on 2 of 2 halves | both halves pay | — | crypto | `FAILS` 0/10 |
| R-06 | some asset class has an edge | positive after costs | 12 instruments, Bonferroni | multi-asset | `FAILS` 0/12, 9 negative |
| R-07 | momentum at registered horizons | clears Bonferroni | 7 horizons | 24 807 S&P bars to 1927 | `FAILS` none clear; φ = −0.0127 |
| R-08 | range breakout (Elliott paper's rule) | clears | 6 variants, Bonferroni | crypto | `FAILS` 0/6 |
| R-09 | some indicator oscillator signals | clears | 56 tests, bar 3.11 | crypto | `FAILS` 0 clear |
| R-10 | MA(5)/MA(34) works | survives 4 named down-markets **and** fresh instruments | confirmation on untouched data | crypto + fresh | `FAILS` 3/5, none significant; reproduced as `ema-50` ⇒ long exposure |
| R-11 | the 4h negative autocorrelation is tradable | realized edge ≥ round-trip cost | breakeven curve | BTC/ETH 4h | `FAILS` 0.78 bps vs ~20 bps, 25× |
| R-12 | the 4h effect is bid-ask bounce | conditional reversion tracks spread proxy | ratio ≥ 1.30 | crypto | `FAILS` — held at 2.61× but sign ordering ran *opposite* |

**R-02 and R-03 are the load-bearing pair.** R-02 established that a negative at this
sample size is informative rather than inconclusive; R-03 established that the bar it
had to clear was nearly double what everyone assumed. Neither was the original
question. Both were asked because a negative result had to mean something before it
could be trusted.

---

## Session 2 — the corrections

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-13 | the 4h/1d negative covariance is a bid-ask spread | G1 γ₁<0 at t<−3.00; **G2** γ₁/var(4h) > γ₁/var(1d); G3 ordered by liquidity; G4 spread vs ~20 bps | 13 tests, Bonferroni 3.00 | BTC/ETH/DOGE/SOL 4h+1d | `FAILS` all four; 4h −0.0436 vs 1d −0.0494, the **opposite** ordering |
| R-14 | the estimator is valid at bar frequency | implied spread must be plausible | frequency walk | 1m…1d | `ANSWER` valid at 1m (**1.19 bps**), inflates beyond |
| R-15 | i.i.d. is a safe null for a real market | measured-φ rate inside 2 se of i.i.d. rate | ±2 binomial se | synthetic, φ injected | `FAILS` (i.e. supported as a finding) — floor **21.8%**, 2.8 pp low, ~9 se |
| R-16 | the detection floor moves with the noise floor | φ=0.05 must stop clearing +5 pp | same ladder, measured null | synthetic | `FAILS` — floor does **not** move; all six searches keep verdicts |
| R-17 | a batch passes if one strategy clears Holm | ≥1 surviving corrected p | N=20 | synthetic | `FAILS` by construction; 4 method-family instead |
| R-18 | an unstable selection is visible to any significance test | batch with interchangeable significant strategies must be flagged | Holm + RC + SPA | synthetic | `FAILS` — PBO = 0.965 is the **only** test that flags it |
| R-19 | a portfolio rule beats random timing | beats the p90 of a null made by permuting each window | 40 null seeds | 15 ETFs, 2010–2026, 4×749 bars, 7 bps/fill | `NOT EVALUABLE` — **the null is degenerate**: `_costed` uses only the window sum and its non-zero count, both permutation-invariant, so all 40 draws are one number and `w >= p90` holds for any portfolio. A flat −50%/bar series also scores 4/4. The 4/4 is **withdrawn**; pinned by `tests/test_portfolio_null_degenerate.py` |
| R-20 | a portfolio rule beats its own members | beats best member, then average | same | same | `FAILS` both — portfolio −1.7005 against best member XLK −0.0479 and equal-weight −1.1858; all 15 members negative, so diversification adds nothing here. **This verdict never touched the null and is unaffected by R-19** |
| R-21 | the negative autocorrelation is crypto-specific | reproduces on MOEX | \|t\| > 3.00, 5 names | MOEX TQBR, daily, no costs | `FAILS` 1/5 — only GMKN, the name that fell 7029 → 118. **Two registered criteria disagree on exactly that name**: \|t\| says 8.54, the 1/√n resolution floor says \|ρ₁\| = 0.0149 < 0.0352. The first run reported 0/5 and was wrong; `resolvable_phi` assumes homoskedasticity and equity returns are fat-tailed (catalogue #15) |
| R-22 | the lineup's ranking carries information | PBO < 0.5 | universe 247 as printed — itself an artifact of error 18; corrected available basis **727** vs explored **64**, 11.4×. 495 splits | 22 strategies × 3 tickers | `FAILS` — **PBO 0.895**; same data at 5 vs 12 folds flips `momentum_roc` −28.3 → +61.1 and moves `best` from `turtle_breakout` to `dual_thrust` |
| R-23 | a batch has any edge at all | RC and SPA reject H₀ | same | same | `HOLDS` p = 0.0020 — *simultaneously* with R-22 failing |
| R-24 | a control with no edge can clear the gate | `random_baseline` must be blocked | 6 independent checks | BTC/ETH/SOL | `HOLDS` — blocked on all six |
| R-25 | `variants_available` can reach the record | field present on disk and reloadable | — | synthetic digest | `CLOSED` — computed, read, never written; then sized by the run's own cap |

**R-23 alongside R-22 is the headline.** The batch shows a detectable aggregate effect
*and* an unusable ranking at the same time. Different failures, different remedies:
aggregate-only strategies might still be tradeable as a portfolio; anything driven by
the ranking — best goes live, best gets capital, top-1 promoted — is a coin flip.

---

## From the parallel session

Carried here because the register should be complete or useless.

| id | hypothesis | criterion | data | verdict |
|---|---|---|---|---|
| R-26 | a ranking on 400 bars approximates one on 1000 | Spearman ρ materially positive | BTC 1h, n=19 | `FAILS` ρ = **−0.337**, p = 0.159; top-5 at 400 **lose** at 1000 |
| R-27 | 4 null seeds describe a window's behaviour | — | BTC 1d | `FAILS` median +252.64 at 4 seeds, **+2.23** at 20 |
| R-28 | correlation dampening runs in the shipped default | dampening applied in `equal` mode | ensemble config | `FAILS` skipped; fixed, regression-pinned |
| R-29 | `reduce_only` reaches the venue | adapter transmits it | execution path | `FAILS` never read `raw`; fixed, pinned at `create_order` |
| R-30 | TSMOM as a **single-asset** rule reproduces the paper | clears its own null | BTC 1d 3000 bars | `FAILS` 1/4 positive, never ≥ p90 |
| R-31 | TSMOM as a **portfolio** rule reproduces | as R-19 | 15 ETFs | `FAILS` H2/H3 — see R-19/R-20 |
| R-32 | demeaning cannot over-credit a non-participant | flat strategy receives 0 | BTC 1d 3000 bars | `FAILS` median subtraction gave +901.76 p95 to a zero-trade strategy; fixed |

---

## Session 3 — the neuroevolution counting check

Registered in `raw-record.md` S3-1 before running (criteria, gain, seed); results
in S3-2. Tool `tools/neuroevolution_count.py`. Space: 50 architectures by
enumeration (depth-0 collapses to logistic; hidden-0/depth>0 keeps its label —
12 functionally tied labels). Panels from one generator (n=2400, train 1200 / val
600 / test 600, gain 0.5, seed 7): signal y ~ Bernoulli(sigmoid(0.5·(x0+x1)/√2)),
noise = permuted signal labels, var identical 0.249950 both. Evolution fixed in
advance: pop 12, 6 generations, tournament 3, top-2 elites carried without refit.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-33 | evolution is countable: distinct < evaluations | distinct < fits | 62 fits / 18 distinct (signal), 62 / 17 (noise) | synthetic, seed 7 | `HOLDS` |
| R-34 | evolution's pick does not survive OOS though signal is findable | (a) pre-specified logistic test ≥ 0.53 AND (b) winner val−test ≥ +0.03 AND (c) winner − ref ≤ +0.01 | 50-arch census + 62-fit evolution, held-out test | synthetic, seed 7 | `FAILS` — (a) 0.5867 HOLDS, (c) +0.0000 HOLDS, but (b) is **−0.0050**: the fitness survived, test joint-best observed anywhere |
| R-35 | PBO over the evaluated architecture population is high | PBO ≥ 0.7 | 18 distinct archs × 16 val blocks, 512 splits | synthetic, seed 7 | `HOLDS` — **0.822** signal (lineup 0.895), 0.965 noise |

**R-34 is the binding negative in the other direction.** The search was expected
to be the problem; it is not, on this space/signal/budget: one shared optimiser,
a smooth weak signal every architecture sees, a fixed val set. The overfitting the
registration aimed at evolution hit the exhaustive census instead —
best-by-val 0.6083 → test 0.5617 (gap +4.7pp) vs the evolution winner's −0.5pp.
Single seed (7), stated as a limitation; re-running seeds until (b) holds would be
the criterion-shopping S2-20 registers against. Variants that might hold H2b
(separate tuners, needle signal, resampled val) are a new registration, not a
reinterpretation.

---

## Session 4 — the portfolio corrected null

R-19 stays `NOT EVALUABLE` and the 4/4 stays withdrawn. New hypothesis, new null,
everything else frozen (15 ETFs, 252/252/40%, 7 bps/fill, 4×749). Full registration
in `raw-record.md` S4-1 (amended pre-run: N2 split into N2a loser-must-fail and
N2b q=1-must-refuse), results in S4-2. Tool: `tools/portfolio_trend.py --null held`
(200 draws/window, seed 361115); the old permuted path is untouched in the same
file and reproduces S2-20 byte-identical. `--selftest` pins N1–N3 with no network.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-36 | the portfolio beats random timing on the held-bars null | total net ≥ p90 of 200 null totals | 4 windows × 749 bars, 200 draws | 15 ETFs, 2010–2026 | `NOT EVALUABLE` — q=1.0000 in all four windows (portfolio never flat across 2996 bars), null spread exactly 0, gate refused before any comparison |

**R-36 is the N2b rehearsal happening live.** The gate that refused the flat
−50% synthetic refused the real portfolio for the same structural reason: a
same-activity timing null for an always-on portfolio is the portfolio itself.
Deeper than either session's first diagnosis: `_costed` sees only (window sum,
non-zero count), and no same-activity timing resample moves either one here.
A cross-sectional or turnover-costed null is a different hypothesis, not a fix —
designing it now would be replacing the null after seeing it gate.

---

## Session 5 — the portfolio cross-sectional null

Second attempt at the open item, registered as S5-1 before running (criterion,
seeds, live design; one pre-run synthetic calibration noted in S5-2 that touches
only the synthetic loser). Tool: `tools/portfolio_trend.py --null cross`
(200 draws/window, seed 362115) plus `--selftest2` (C1–C3, no network). Shared
scorer for observed and null subsets; reproduction assert (scorer == port_net)
held in all 4 windows.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-37 | the portfolio beats random instrument selection | total net ≥ p90 of 200 null totals | 4 windows × 749 bars, 200 draws | 15 ETFs, 2010–2026 | `FAILS` — total −1.7005 vs null median −1.1904, p90 −0.9895; 1/4 windows (record only) |

**R-37 is the first evaluable answer to the portfolio-timing question, and it is
negative.** Random same-size instrument subsets lose less than the TSMOM
selection. The open item is CLOSED: timing has no variance to test (R-36),
selection loses to random (R-37). H2/H3 stand as FAIL — all three portfolio
claims now fail with evidence, none withdrawn.

---

## Session 6 — signed order flow

Open item, unblocked by re-checking rather than assuming: Binance raw klines
(12 fields, taker-buy base volume at index 9) are reachable from here via the
installed ccxt's implicit `public_get_klines` — no new dependency, research
tool only (`tools/orderflow_imbalance.py`). Registered S6-1 before running
(C0 field check gating K1/K2 gating H); results S6-2.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-38 | trailing taker imbalance predicts next-bar BTC return | \|t\| > 3.00, lags {1,4} | 2999 bars, 2 lags | BTCUSDT 1h spot klines | `FAILS` — t = −1.29 / −1.16; C0 +0.4086 (fields real) |

**R-38 closes the item negatively with evidence.** The endpoint exists and the
blocked question is answered: no predictability at these lags. (The Almgren
permanent-vs-temporary split stays untestable — it needs our own fills, and
that is the last open item, not this one.)

---

## Session 7 — neuroevolution replication

R-34's falsification of H2b stood on one seed (7). Replication registered S7-1
before running (frozen design, fresh seeds 8–12); results S7-2. No new code —
`tools/neuroevolution_count.py --seed` — so no new pin tests; the S3 pins cover
the instrument.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-39 | R-34 replicates: the winner's fitness survives on fresh seeds | gap < +0.03 on ≥4/5 seeds | 5 × (50-arch census + 62-fit evolution) | synthetic, seeds 8–12 | `HOLDS` — 4/5 (gaps +0.025/−0.027/+0.015/+0.033/−0.043; seed 11 misses by 0.33pp while tying census-best) |

**R-39 makes the non-closure of the ML lead a 6-seed statement.** The search
transfers on this space/signal/budget; H1/H2c/H3 re-hold on all five seeds, and
twice evolution matches or beats exhaustive selection OOS. What could still
break it is a different registration, not a reinterpretation.

---

## Session 8 — the ensemble, the usable form

Constructive finish, registered S8-1 before running (frozen design, fresh seeds
13–17; additive ensemble lines, no existing number moves — S3 pins re-run
green). Results S8-2.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-40 | averaging the evaluated population transfers; the pick adds nothing | ensemble ≥ 0.53 AND \|ens − winner\| ≤ 0.02 on ≥4/5 seeds | 5 × (50-arch census + 62-fit evolution) | synthetic, seeds 13–17 | `HOLDS` — 5/5 both (max gap 1.0pp; census-50 ensembles agree) |

**R-40 finishes the ML arc without breaking anything.** Search transfers,
ranking is noise, average is the usable form — 11 seeds, one optimiser, zero
real data. Seed 16 (winner val lucky at +5.8pp, test and ensemble both 0.5717)
shows luck moves val, never the OOS answer.

---

## Session 9 — ML on real data

Boundary crossed deliberately as its own hypothesis, registered S9-1 before
running (same space/search/ensemble; 6 real features = lagged BTC 1h returns;
6000 bars train 3600/val 1200/test 1193; costs inside the criterion). Results S9-2.
Tool: `tools/neuroevolution_count.py --stage main --data btc1h --seed 41`
(leakage-pinned builder, permutation control twin).

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-41 | the ML ensemble transfers on real BTC net of costs | ensemble ≥ 0.53 AND test net > 0 | 50 archs + 62 fits, held-out 1193 bars | BTCUSDT 1h, 6000 bars | `FAILS` — 0.5264 (misses by 0.36pp, not rounded up) and net −0.0858; census mean 0.5055, PBO 0.986 |

**R-41 closes the ML lead in both directions.** Synthetically the search works
(R-34/R-39/R-40); on real data it finds nothing — the only honest reading after
40 edgeless hypotheses. No plugin in either direction: one side does not work
where it must, the other only where the answer was planted.

---

## Session 10 — gate hardening (engineering, not a hypothesis)

`raw-record.md` S11. No new verdict: these are defects in the instrument and
the enforcement path, fixed and pinned. Missing evidence no longer defaults to
a pass (p=0.0, clean CI, unavailable PBO); `max_pbo` is live; and the two paths
that decide what actually trades — Consilium startup and `SET_ENSEMBLE` — now
call `evaluate_many`, so Holm/RC/SPA/PBO reach the live lineup. A one-member
lineup has no batch to fail and is reported `not_applicable`, not blocked.

This changes the meaning of the existing verdicts, not their sign: the batch
was already `unstable_selection`; now the product obeys it. Full suite 1062
passed; ruff clean. Remaining known defects from the audit, not yet fixed:
PBO's rank-loss formula inflates the estimate (definition gap measured and
documented in S14; deliberately not changed — switching it would disable the
instability check at the fixed 0.5 threshold and needs its own registration);
`universe` (234/727) is computed but never enters the default correction
(the capability now exists but enabling it would move every verdict); research
measures on 1d bars while execution runs on 1m forming bars.

---

## Rules derived, written down so they are not rediscovered the hard way

Each cost a real experiment to learn. Each is a **precondition for the next
registration**, not a lesson.

1. **Check that the null can vary.** R-19's null was built by permuting the window it
   tested, and the statistic it compared depended only on the window's sum and its
   non-zero count — both permutation-invariant. All 40 draws were one number, so
   `observed >= p90` was arithmetic and held for every portfolio, including a flat
   −50%-per-bar series. **A null invariant under the transformation it applies cannot
   reject anything, and its agreement with the observed value is indistinguishable
   from a pass.** Look at the spread of the null before reading any comparison
   against it. The table was four columns wide and agreed to four decimal places;
   that agreement *was* the defect.

2. **A relative test cannot be a profitability criterion without a zero benchmark.**
   This was the first diagnosis of R-19 and it was incomplete. The rule is real — but
   R-19 never reached it, because the null had no variance to be relative to. A rule
   inferred from a broken instrument is not safe to generalise from, and this one was
   nearly written down as if it had been established cleanly.

3. **Validate an instrument on a known-answer case before reporting from it.** Every
   expensive wrong number here was a plausible value from an unvalidated tool: p = 0.975
   for a mean of 3.0 with a tight interval excluding zero; +120% on a
   zero-autocorrelation series; +901 bps at p95 to a strategy with zero trades; a
   Reality Check passing 4/4 on a losing portfolio. All four were caught by
   known-answer tests, and none would have been caught by reading the code.

4. **Report evaluations *and* distinct candidates.** They answer different questions
   and they diverge whenever a search re-evaluates correlated candidates. For a grid
   this is trivial; for evolution it is the whole difficulty.

5. **Census the space before searching it.** R-30 would prove nothing against noise —
   a search failing to find a signal that is not there says nothing. Enumerating the
   whole space first establishes that a good answer exists, and only then does a
   search failure say something about the search.

6. **A correction computed over the explored count is lenient by exactly the ratio
   between explored and available.** 64 against 727 here. Both numbers have to be on
   the page.

7. **Check that a "not applicable" result is not being reported as a negative one.**
   20 strategies once read as 20 refutations when no test had run. The converse bit
   here: `NOT EVALUABLE` is not a soft `FAILS`, and a withdrawn result is not a quiet
   edit — the withdrawn number stays in the record with its reason.

8. **When two readers disagree about the same data, the disagreement is about a
   reader.** It was the alphabetical position of an empty base class, and neither
   record could have explained it alone.

9. **Write the result down when it is produced, not when a document needs it.**
   R-19's numbers existed for two sessions in a commit message and a docstring. They
   were recovered by re-running the tool, which cost a Yahoo download; had the tool
   been broken by then, the numbers would have been unrecoverable and the error would
   have become permanent. This is error #17's class, committed by the session that
   catalogued #17.

---

## Session 11 — the two remaining gaps, closed with evidence

Registered S16/S17/S17b in `raw-record.md` before running; results S16-2/S17-2.
Tools: `tools/universe_correction_verdict.py`, `tools/timeframe_verdict.py`.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-42 | available-space correction changes who trades | live_ok count drops when Holm uses U instead of batch N | U=234 vs N=20, 20 strategies | live clearance store | `FAILS` — 0 → 0; adjusted p min 0.160 → 1.000 |
| R-43 | 1m research reveals what 1d missed | no positive 1m net after costs | 4000 bars required | BTCUSDT 1h/1d | `NOT EVALUABLE` — only 3387 daily klines; window NOT shrunk |
| R-44 | feasible-window replication | no positive 1m net after costs | 3000 bars, 20 strategies | BTCUSDT 1m + separate 1d control | `HOLDS` — 0 positive, 2 never traded |

### The existential claim (R-45)

Registered S18 after the user asked to prove "at any moment at least one
profitable strategy exists" — phrased so that best-of-N would make it
trivially true. The registered version uses 20 default strategies, five fixed
600-bar BTCUSDT 1h windows, 7 bps/fill, 1000 USD notional, and a gated count
requiring BOTH positive net and net above a same-activity random-timing null.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-45 | every window contains a gated profitable strategy | ≥1 gated in all 5 windows | 20 strategies × 5 windows | BTCUSDT 1h, 6000 bars | `FAILS` — window 5: raw 0, gated 0 |

A known-answer check caught the first implementation counting 15 "gated"
strategies in a window with zero positives; the predicate was fixed to require
profitability and pinned by `tests/test_existential_gate.py`. The failure is a
statement about the DEFAULT universe on these windows, not a general theorem.
ML stays a separate open question: R-41 was one family/one window, not a
general negative.

### Long-horizon ML (R-46)

Registered S19; results S19-2. Same 50-architecture space, optimiser, evolution
and ensemble, moved to BTCUSDT DAILY bars, 2993 rows, train 2000 / val 400 /
test 593 (~20 months). Known-answer gate passed before the run.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-46 | the ensemble transfers and trades profitably on a yearly horizon | net > 0 AND accuracy ≥ 0.53 on held-out | 62 evaluations / 22 distinct, 593-day holdout | BTCUSDT 1d, 3000 bars | `FAILS` — accuracy 0.5025, net −0.4295; PBO 1.000; census mean 0.4908 |

Second real-data ML failure (R-41 hourly, R-46 daily) and the first on a yearly
horizon. Boundaries are one instrument/feature family/architecture space; a
different panel or selector is a new registration, not a reinterpretation.

### Horizon ladder (R-47)

Registered S20; result S20-2. Four nested daily holdouts 60/120/240/360 bars
ending on the same bar (train 600 / val 100), same model family.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-47 | transfers at every short horizon | all four: net > 0 AND accuracy ≥ 0.53 | 4 × (50 archs, 62 fits) | BTCUSDT 1d, 1200 bars | `FAILS` — all four miss both bars; PBO 0.911–1.000 |

Direct answer to "different horizons": 2/4/8/12 months all fail. The forming
daily bar in the research fetch is recorded as a caveat; the verdict is
insensitive to it.

### Composite strategies and stop-loss (R-48/49/50)

Registered S21/S22/S23 with pair universe, stop parameters and correction
fixed before the respective runs.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-48 | a composite entry/exit pair is profitable at every horizon | ≥1 of 6 pairs: net > 0 and Holm p < 0.05 in all 4 windows | 6 pairs × 4 windows | BTCUSDT 1d, 1200 bars | `FAILS` — one window only; best pair positive in all four but uncorrected |
| R-49 | a 2% stop + 10-bar recompute improves the pair | stop net > baseline and Holm p < 0.05 in all 4 windows | same 6 pairs × 4 windows | BTCUSDT 1d | `HOLDS` — dual_thrust→adx_di_trend passes all four |
| R-50 | the rule transfers to other instruments | same pair/params on ETH and SOL, all 4 windows each | same | ETHUSDT, SOLUSDT | `FAILS` — ETH misses one, SOL misses all |

First positive strategy reading in the project (R-49), immediately bounded by
a failed generalization (R-50). NOT promoted to live: one instrument, nested
windows, one pre-fixed six-pair universe.

### Chaos-derived features (R-51)

Registered S24; result S24-2. Local causal implementation inspired by
`aphina-server`'s numeric engine (delay embedding + Lyapunov-style divergence,
box dimension, spectral entropy) replaces the six raw lags in the same
50-architecture ensemble. Known-answer causality tests pinned first.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-51 | chaos features transfer and trade | net > 0 AND accuracy ≥ 0.53 on ≥1 timeframe | 62 evaluations / 20–22 distinct per timeframe | BTCUSDT 1h + 1d | `FAILS` — 1h net −0.1380/acc 0.5205; 1d net **+0.1083** but acc 0.5059 |

First positive ML net on real data, but below the registered accuracy bar, so
it is not a pass. No threshold was tuned after the result.

### Time-disjoint check (R-52)

Registered S25; result S25-2. Same R-49 pair/stop/cooldown on an earlier,
strictly disjoint 1200-bar BTCUSDT daily block (2020-05 to 2023-08).

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-52 | the rule clears all four windows on disjoint history | net > 0, beats baseline, Holm p < 0.05 in all 4 | 6 pairs × 4 windows | BTCUSDT 1d, earlier 1200 bars | `FAILS` — 2 of 4 windows pass; stop improves net in all four |
| R-53 | one stop configuration survives all four windows | same pair, 9 configs (1/2/3% × 5/10/20), Holm over 9, same config in all 4 | 9 × 4 windows | BTCUSDT 1d, earlier 1200 bars | `FAILS` — no configuration intersects all four; two criterion defects caught and pinned |
| R-54 | the gate can detect an injected weak edge | 2 bps per held bar must produce a pair passing all four; 0 bps is the control | 6 pairs × 4 windows × 4 edge levels | same earlier block, P&L-only injection | `FAILS` — even 5 bps gives no all-window pair; gate INCONCLUSIVE |

**R-54 changes how the previous failures may be read.** R-48/50/52/53 remain
FAILS of their registered criteria, but the gate cannot detect a weak edge at
the registered magnitude, so they are NOT evidence that no edge exists. This
is the R-02 power discipline applied to the composite family; it does not
promote R-49 and does not prove an edge either.

### Aggregate with a correct control (R-55/56)

Registered S28/S29. R-55 FAILS its own criterion because its "0 bps" control
was a false premise (zero injection is the real net, not a no-edge arm) — but
its numbers established that the aggregate statistic HAS power: net positive
in all four disjoint windows, p 0.006/0.002/0.002/0.002, 2 bps injection 4/4.
R-56 rebuilt the control correctly (random timing and shuffled returns).

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-55 | aggregate passes with clean 0-bps control | control must be clean at 0 bps | 6 pairs × 4 windows × 3 edges | BTCUSDT 1d, earlier block | `FAILS` — false control premise; statistical numbers retained |
| R-56 | aggregate beats constructed no-edge controls | REAL ≥3/4, RANDOM and SHUFFLED ≤1/4 each | 3 arms × 4 windows | same earlier block | `HOLDS` — REAL 4/4, RANDOM 0/4, SHUFFLED 1/4 |

Strongest result to date, with a demonstrated-power statistic on disjoint
data. Still one instrument and an aggregate, not a single tradable strategy:
other instruments and a later non-overlapping span need new registrations.

### Aggregate confirmation (R-57)

Registered S30; result S30-2. Same aggregate and controls on ETH earlier,
SOL earlier and BTC later blocks. REAL passed 4/4 on all three arms, but the
controls also passed too often: ETH SHUFFLED 3/4, SOL RANDOM 3/4 and SHUFFLED
3/4, BTC SHUFFLED 3/4.

| id | hypothesis | criterion, fixed in advance | denominator | data | verdict |
|---|---|---|---|---|---|
| R-57 | aggregate beats constructed controls on three additional arms | each arm: REAL ≥3/4, both controls ≤1/4 | 3 arms × 3 controls × 4 windows | ETH/SOL earlier, BTC later | `FAILS` — all three arms; aggregate net reproduced by random timing or shuffled returns |

R-56 is therefore a single-block result, not a general one. The shape matches
R-22/R-23: a positive aggregate that fails the control asking whether timing
contributed anything. No promotion.

With R-50 (ETH/SOL), this bounds R-49: the first positive strategy reading is
specific to the discovery market and windows, and is NOT promoted to live.

### Stop-parameter mechanism (R-53)

Registered S26; result S26-2. Nine fixed stop/cooldown configurations on the
disjoint block, Holm over nine, criterion requiring the SAME configuration in
all four windows. Two known-answer criterion defects (per-window "any winner"
and an empty-window vacuous pass) were caught and pinned before accepting the
result. Verdict: `FAILS` — no configuration passes all four, though the stop
improves net in every window. This explains R-52 without promoting any setting.

**Both gaps are now resolved without touching a default.** The Sullivan
correction moves every p-value to the floor but there was no `live_ok`
strategy to demote, so its registered criterion fails; the capability stays
optional, the default stays batch-N. Moving research to the execution
timeframe cannot manufacture an edge either. Absolute P&L figures from the
timeframe harness use one-unit sizing and are NOT cash P&L — only their sign
is comparable; this is stated in S17-2 so nobody quotes the large 1d numbers.

---

## Status

```
registered hypotheses            57
closed with evidence             50   (FAILS, HOLDS, ANSWER, CLOSED)
not evaluable                     3   R-19 (null degenerate), R-36 (null gated:
                                      portfolio never flat, q=1.0000), R-43
                                      (3387 < 4000 registered daily bars) —
                                      none counted as pass or failure
open                              1
```

50 + 3 + 1 = 54, not 57: the resolved `neuroevolution counting check` item
became THREE rows (R-33/34/35), the resolved portfolio-null item took TWO
(R-36/37), the two remaining gaps took THREE (R-42/43/44). R-45 (existential
claim), R-46 (long-horizon ML), R-47 (horizon ladder) and R-48/49/50 (composite,
R-51 (chaos features), R-52 (disjoint check), R-53 (stop grid), R-54 (power
check), R-55/56 (aggregate and control) and R-57 (confirmation) were added
later as separate registrations. Counted this way on purpose rather than
silently.

The last open item:

| open | blocked on |
|---|---|
| fee schedule | reconciliation runs hourly, but it has never seen a real fill — the schedule is assumed, not measured |

```
error catalogue          this session   parallel session
  entries                          19                 23
  verified closed in this tree     19                  5
```

The two catalogues are **not additive** — a research tool writing to the live gate
appears as catalogue #11 here and as `E14` there. The counts are reported separately
because neither session read the other's record while it was being written; that is
the finding in [`parallel-session.md`](parallel-session.md) §"What neither session has
verified", not a defect in either.

**No hypothesis in this register returned a tradable edge.** The productive outcomes
were four instruments that were wrong: the bootstrap p-value, the denoiser, the
verdict reversal, and the portfolio null — each found by a check whose answer was
already known.

---

## Correction log for this document

This register was assembled by reading the other documents rather than re-running
anything, which is exactly the transfer step that corrupted the two sessions' records
once already. Four errors were caught in it before commit:

| written | actual | how it was wrong |
|---|---|---|
| R-22 universe `727` | run printed `247`; `727` is the separately-measured available space | two different quantities merged into one denominator |
| R-22 "12 of 19 signs flip" | that is `M3`, R-26 — a window-length result, not a fold-count one | a number from the wrong experiment attached to the right-looking row |
| "defects found and fixed 32" | 19 and 23, verified-closed 19 and 5 | hypothesis count restated as a defect count |
| R-19 criterion "≥2 of 4 windows ≥ p90" | the registered text was "beats random timing on the calibrated null"; the window form was an operationalisation | the observation written into the criterion column |

A fifth was caught by the same pass and not written: the `25×` shortfall in R-11 is the
figure in `raw-record.md`; error #16 records the per-trade figure as 25.6× against
12.8× per-fill. R-11 carries the 25× and the distinction stays where it was registered.

### Then a fifth correction, of a different kind

Writing this register required numbers the documents did not contain. R-19's result
existed only in a commit message and a tool docstring — the same omission as error
#17, by the session that catalogued #17. Recovering it meant **re-running the tool
rather than citing the summary**, and the run showed the `portfolio` and `p90 of null`
columns agreeing to four decimal places in all four windows.

That agreement was the defect: the null is built by permuting the window, and the
statistic compared depends only on the window's sum and non-zero count, both
permutation-invariant. R-19's verdict moved from `HOLDS / CRITERION DEFECTIVE` to
`NOT EVALUABLE`, and the earlier relative-test diagnosis is demoted to rule 2 with a
note that it was inferred from a broken instrument.

Had the numbers been written from the summary instead of re-derived, this would have
been committed as a valid finding — a relative-test weakness, correctly reasoned, about
a test that could not reject anything. **The gap in the documentation was not a
bookkeeping omission. It was the only reason the defect was still findable.**

Keeping this log matters more than the corrections do. A register built by reading
other registers will drift from them the same way, and the drift is invisible to
anyone who does not re-read the sources — which is why the third column exists.