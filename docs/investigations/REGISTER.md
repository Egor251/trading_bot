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

## Status

```
registered hypotheses            32
closed with evidence             27   (FAILS, HOLDS, ANSWER, CLOSED)
not evaluable                     1   R-19 — the null had no variance; withdrawn,
                                     NOT counted as either a pass or a failure
open                              4
```

The four open items, none of which is a strategy awaiting a verdict:

| open | blocked on |
|---|---|
| signed order flow | `fetch_ohlcv` returns 6 columns; Binance taker-buy volume (raw kline 9–10) is discarded upstream. Needs an exchange-specific raw endpoint |
| fee schedule | reconciliation runs hourly, but it has never seen a real fill — the schedule is assumed, not measured |
| portfolio hypothesis, corrected null | needs a registered null that resamples *which bars are held*, so the cost term varies. Not written, deliberately: replacing a null after seeing it degenerate is the criterion-shopping registered against |
| neuroevolution counting check | registered, not run — see [`HANDOFF-neuroevolution.md`](HANDOFF-neuroevolution.md) |

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