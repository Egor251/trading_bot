# Investigation narrative: the reasoning chain, not the results

Companion to `log.md`, and indexed by [`REGISTER.md`](REGISTER.md) — every hypothesis
below carries an R-number there. This file says **what we were thinking between the
measurements**, why each next step was chosen, and what made a result change our mind
or fail to.

Same sourcing rule. Session 2 is direct. Session 1 is reconstructed from its summary
and is marked where the reasoning is second-hand — the *decisions* survive, the
*hesitations* do not, and pretending otherwise would be inventing them.

---

## Part one — session 1, second-hand

### Six searches, all negative

We registered a hypothesis, fixed the criterion before looking, corrected for
multiplicity, ran it, and got nothing. Six times.

```
ceiling (is the market predictable at all)   negative, nothing above φ = 0.05
10-window design                             28/210 raw, 30 demeaned, zero at 10/10
asset-class sweep (12 instruments)           0/12 positive, 9 negative
momentum, 24 807 S&P bars back to 1927       none of 7 horizons clears, φ = −0.0127
indicator panel                              56 tests, 0 clear
breakout, rules from an Elliott paper        0/6
```

**What the results did to us:** nothing, at first. Six negatives in a row is
consistent with two very different worlds — a market nobody can predict, or a method
that cannot see what's there. For a long stretch the log implicitly assumed the first
and never said so.

That is the most expensive silence in the whole record.

### So we measured the instrument, not the market

If the method is the problem, the fix is to measure the method. `null_calibration`
answers two questions we had been assuming answers to.

**The false-positive rate.** How often does the comparison report noise as signal?
Nominal p90 says 10%. Measured: **12.3%** for independent windows, **19.5%** with a
shared market factor per window, **22.0%** with a factor carried across windows.

The middle number is the real case, and it is nearly double the nominal one. Every
"finding" ever reported by this method has to be discounted by it.

**The power.** Before concluding anything from nulls, ask whether the null could ever
have seen a real edge. Inject φ of known size, measure the detection rate:

```
φ      snr     detection above floor
0.05   0.319        +6.5 pp   ← smallest detectable edge
0.10   0.638       +14.3 pp
0.20   1.277       +32.0 pp
0.30   1.915       +48.7 pp
```

φ = 0.05 lifts detection 6.5 pp over the floor. So a negative at this sample size is
*informative*, not merely inconclusive.

**The shape of that relief, though, is what we did not notice yet.** We used the power
table to justify reading anything as a real signal, and the noise floor to discount
anything that wasn't. Both numbers were measured against an i.i.d. null. Both would
move, later, in the same direction.

### A window count that decides whether a claim is even expressible

Ten windows. The reasoning is pure arithmetic and needs no simulation: a strategy right
in every window has p = 2^−W, and it has to beat 0.05/S after correcting for S
strategies measured.

```
W=4   2^-4  = 0.0625   vs 0.00238   unreachable
W=8   2^-8  = 0.0039   vs 0.00238   unreachable
W=10  2^-10 = 0.00098  vs 0.00238   reachable
W=16  2^-16 = 0.00002  vs 0.00238   reachable
```

Below ten windows, **no result of any kind can clear Bonferroni at 20–21 strategies** —
not a good one, not a perfect one, not a rigged one. That is not a property of the
market or of the data. It is arithmetic, and it set the minimum design for everything
after.

It also produced the flattest table in the project: per-window detection did not
improve from 4 to 32 windows (70.5%, 68.5%, 68.7%, 68.5%). p90 is judged inside one
window against one null, so window count is not in that calculation at all. More
windows buy the *ability to make a claim*, not detection. An earlier draft of that
function claimed the opposite and was wrong because it measured a family-level
statistic under a per-window heading.

### EWO: the first thing that looked like it worked

A rule extracted from an RBC explainer — not its conclusions, the rule. MA(5)-vs-MA(34).

```
BTC  +334.3%    ETH  +405.6%    daily
```

**We did the obvious thing and it passed the obvious test.** Four named down-market
periods were chosen in advance — including the Great Depression, where it returned
+88.4% while the S&P was −77.5%.

Then we asked the question the paper had not: does it work on instruments it was not
chosen on?

**3 of 5 fresh instruments held. None significant.**

And the indicator panel — run independently, 56 tests — reproduced it as its `ema-50`
row. That was the tell. Not a signal: **long exposure**. The 334% is what holding a
long position in a rising market looks like when nothing is subtracted for the path
taken to get there.

### The thing that was actually there

Mechanism testing — *why would this work at all?* — turned up something none of the six
searches had been looking for:

**4h first-order autocorrelation, negative, t = −3.44** on 20 004 BTC bars; −3.01 on ETH.

A real, significant, reproducible statistical finding. The market leans against its own
last move, at a horizon nobody had asked about.

**Why this reframed everything:** every prior negative had been about strategies. This
was about the *series*. If the price series is predictable, the ceiling argument was
wrong and the searches were measuring in the wrong place. If it is microstructure, the
6 searches were measuring noise and never had a chance.

### Is it tradable? The breakeven curve

A dependency is not an edge. It has to survive costs.

The rule: measure the breakeven cost per fill implied by φ and σ, and compare with what
trading actually costs.

**We got the answer wrong the first time and it mattered.** The effect was described as
worth about 15 bps per trade — "a pricing problem", an execution problem, something an
operator could fix.

The breakeven curve said **0.6 bps per fill**. A **25× shortfall**, not 1.3×. The
correction was issued explicitly rather than quietly amended, because a number that was
already in the record had to be corrected in the record too.

*This is the moment the project turned from "looking for strategies" to "measuring
what it costs to be wrong".*

### And the cost total was broken anyway

While wiring costs, a self-check on a known-answer series — one with zero
autocorrelation by construction — returned **+120%**.

A cost total was being reset on every position open, so net equalled gross. Every
result that had ever reported net P&L was reporting gross.

---

## Part two — session 2, direct

### The question we had never asked about our own code

Asked of the product: *can it do the runs and tests we are doing by hand?*

The honest answer was partly yes. It had data, execution, costs, sizing, walk-forward
with a held-out tail, a block bootstrap. It also had `adjust_pvalues` — implemented,
tested, exported, and **called from nothing but its own tests.**

That was the shape of everything that followed. Three functions, three manifests, one
prometheus exporter, one fee reconciliation: all correct, all unreachable.

The gate asked, per strategy, "does this interval clear zero?" — and ran that test 28
times without paying for it. At 95% and N=28 that is ~1.4 expected false positives even
if every strategy is pure noise. Ranking the survivors promotes the luckiest one.

`apply_multiplicity` went in. Then three properties, each pinned by a test, because
each is a way this could have looked correct while being permissive:

- **A missing p-value blocks rather than passes.** Clearance records written before
  p-values existed have *unknown* evidence, and unknown is not a pass.
- **Only measurable rows count toward N.** Counting unmeasurable rows penalises a batch
  for its own missing data — tightening the bar exactly when evidence is thinnest.
- **The correction can only withhold.** A correction that quietly rescues weak evidence
  is worse than none, because it looks rigorous.

### The first p-value we wrote was wrong, and it failed silently

The obvious way to get a p-value from a bootstrap is to ask where the observed mean
falls inside the bootstrap distribution.

It reported **p = 0.975 for a mean of 3.0 with a confidence interval of [2.93, 3.09]**.

A bootstrap distribution is centred on the observed statistic *by construction*. The
observed mean sits at its midpoint, so the answer is ≈ 1 for every input, including one
where the signal is so strong the interval barely comes close to crossing zero. The
shortcut produces a monotone, right-ranged, right-united number that grades strong
signals as evidence against themselves.

A p-value is the probability of the data *under the null*, so the null has to be in the
resample. Recentring the series on zero before resampling imposes it while the block
structure keeps the autocorrelation intact.

Calibrated: **4.3% rejections at a nominal 5%** on 300 runs of i.i.d. noise; 100%
detection on a shifted mean; floored at (1+count)/(1+n) so a strong signal never
reports exactly zero. The calibration test exists because the shortcut passes every
other test in the file.

*Every numerical claim in this document that we did not check against a known answer
is suspect. That one had a known answer available and we didn't use it.*

### The cost model is mostly fees

Applying Almgren's permanent-vs-temporary split to this system's cost model produced
nothing sensible, and working out why was the useful part.

A round trip is charged `fee_bps: 5` + `slippage_bps: 2`. Against the spread measured
at 1m, the fee is **71.4%** of the charged cost.

A fee is a transfer to the venue. It does not scale with size, does not decay when the
order completes, and no execution schedule touches it. Calling it permanent impact
overstates how much of the barrier is addressable; calling it temporary implies a
scheduling fix that does not exist.

**And two cost numbers were live at once.** The research sweeps hardcoded 10 bps per
fill at nineteen call sites while the product charges 7 — research running 1.43× more
pessimistic than the system it documents.

Then a units error of our own: the edge is per *trade*, the cost is quoted per *fill*.
Compared directly, 20/0.78 reported as 12.8× where the round-trip figure is 25.6×.
The 25.6× was right. The correction was to put the conversion inside the function so the
raw numbers cannot be compared again.

**What survived the correction:** at a zero fee the measured spread alone is still 1.5×
the edge. The 4h reversal rule still does not pay. What changed is that the barrier is
about half what was quoted — and no execution improvement closes it.

### Roll: we picked it to confirm, and it refuted

The strongest-looking lead remaining. Roll (1984) says negative first-order covariance
in an efficient market is a *measurement* of the spread, recoverable from prices alone.
That is exactly the diagnosis our 4h finding needed.

Criteria registered before data. All four failed.

```
ρ₁ = −0.0436 at 4h, −0.0494 at 1d   the LONGER horizon is MORE negative
implied spreads 60 bps (4h) to 157 bps (1d)
```

The bounce prediction is the opposite: averaging a mechanical bounce over more bars
washes it out, so ρ₁ per unit variance must *fall* with horizon. It rose.

Three bugs in our own instrumentation, and the only reason we found them is that the
first run reported **4175 bps of spread**. That is 42%. It is not a plausible reading
of anything, and implausibility is a signal:

- units — γ₁ is built from log returns, so its square root is already return-scale; we
  divided by σ again and inflated everything 200×;
- sign — `t < ROLL_GATE_T` with a positive bar and a negative t is always true, so the
  significance column passed every negative reading;
- normalisation — code said `gamma1/var**2` against a registered `gamma1/var`.
  Corrected **to the registered form and left there**, because moving it back after
  seeing which direction helps is criterion-shopping.

Then we walked the frequency, and that settled it while validating the instrument on
the way:

```
1m   ρ₁ −0.0239   implied spread   1.19 bps   ← believable BTC spread
5m   ρ₁ −0.0254                    3.49 bps
4h   ρ₁ −0.0515                   35.47 bps
1d   ρ₁ −0.0473                  103.82 bps
```

At 1m the estimator works. It stays sane at 5m and then inflates without limit.

**The bounce is real, small, and lives where the estimator is valid. The 4h and 1d
effects are not it.**

We had chosen this paper to confirm an explanation and it closed the explanation
instead. That is the pre-registration doing its job: the criterion was fixed before we
knew, so the outcome was binding rather than negotiable.

A test on the estimator recorded a subtlety worth keeping: an i.i.d. ±amplitude series
has γ₁ **exactly zero**. Negative covariance needs the bid-ask side to *alternate* —
consecutive independent signs have no covariance however large the amplitude is. An
estimator that reported a spread for both would explain everything and therefore
nothing.

### The tool that rewrote the gate without saying so

Before promoting the measured clearance we checked whether we would *reduce* the gate's
fidelity. The live store already had twenty entries carrying today's date and our
exact p-values.

`phantom_service.run_walk_forward` called `record_digest_clearance` with no path, so it
wrote to `DEFAULT_CLEARANCE_PATH` — an absolute path inside the repository. Redirecting
`TRADING_DATA_DIR` into a sandbox does not redirect that. There was no switch. The
surrounding `except: pass` would have hidden a failure.

Our research tool, whose docstring said it "writes to its own clearance file, never the
live one", had written 20 entries to the live store. Two further runs inflated
`variants_tried_lifetime` from 3 to 12 — **the Sullivan denominator, polluted with the
experiment's own repetitions.**

The docstring was confident and false. A measurement that changes what trades without
saying so is not a measurement.

### Trimming that did nothing and looked like protection

Hansen's SPA trims the worst alternatives before taking the maximum. We implemented it
that way.

Trimming the *worst performers* from a **maximum** is arithmetically a no-op —
removing items from the bottom cannot change the largest. It produced byte-identical
results at trim 0 and trim 0.3, and it looked like a safeguard because nobody checked
that it changed anything.

Trimming by *reliability* — largest standard error relative to effect — has a real
effect and matches the paper's actual motivation, "reduces the influence of erratic
forecasts". The no-op is now pinned by its own test so it cannot come back as
protection.

### The verdict that was inverted twice

`reject` in a Reality Check is named from the *test's* point of view: the null was
rejected, which means an edge was found.

Read as "the strategy was rejected", it inverts everything. A batch of noise came back
as having an edge; a batch with one dominant strategy came back as having none. The
gate was backwards.

It is easy to get wrong because the per-hypothesis correction runs the other way — it
makes it *harder* to call anything significant — so "noise" and "rejected" feel like
they belong together. The tests that mattered were then written to pin **both**
directions in one test, because asserting only the expected direction let the mistake
through twice.

### Asking about our own lineup, for the first time

Every negative result in the log is a statement about individual strategies. None of
them was ever a statement about the **ranking**.

`tools/lineup_verdict.py` — 22 strategies, 3 tickers, walk-forward, all four
corrections on the batch.

```
universe 247
Reality Check  p = 0.0020  reject       a detectable batch effect
SPA            p = 0.0020  18 after reliability trimming
PBO            0.895       495 splits   worse_than_random
verdict        unstable_selection
```

**PBO = 0.895.** The in-sample winner finished out-of-sample below where it started in
89.5% of re-splits. A coin toss is 50%.

Then the stronger evidence. The first run used 5 folds, the second 12, changing nothing
else:

```
momentum_roc              −28.33  →  +61.06
volume_impulse_example   −110.91 (p=0.001)  →  −48.94 (p=0.429)
turtle_breakout           +74.12 (p=0.050)  →  +100.80 (p=0.005)
best strategy            turtle_breakout  →  dual_thrust
```

Same strategies, same data, one parameter different, and the winner changes. That is
PBO made visible without any statistics.

**Why this is not "nothing cleared"** — that was already known. It is that the batch
shows a detectable aggregate effect *and* an unusable ranking simultaneously, and those
are different failures with different remedies. Aggregate-only strategies might still
be worth trading as a portfolio. Anything driven by the ranking is trading a coin
flip.

Folds went 5 → 12 for measurement resolution and nothing else was touched. CSCV needs
as many observations as it splits into; at 5 folds it returned `insufficient_series`
and H3 could not run at all.

### The universe, and a reader that hid strategies by alphabet

Sullivan, Timmermann & White in one line: correct over the space the rules were drawn
from, including the ones never reported.

`variants_tried_lifetime` had been in the clearance store the whole time, commented
"the denominator a deflation argument needs", read by nobody.

Then we tried to compute the *available* space rather than the explored one, and got
two different answers from two passes over the same data: 16 schemas and 7 without,
then 14 and 9. And `supertrend_follow` was in the second list of misses — while its
source defines `CONFIG_SCHEMA` on the class in plain sight.

The reason: **`StrategyPlugin` declares `CONFIG_SCHEMA = {}` as a placeholder.**
Discovery took the *first* class with a dict schema, and `dir()` on a module is
alphabetical — so it stopped on the base class and never reached
`SupertrendFollowPlugin`, which sorts later. Which strategies were missed depended on
**alphabetical position**.

In `phantom_service` the empty dict was accepted as real, making
`variants_available == 1` for every strategy — the Sullivan denominator switched off
across the whole repo while continuing to look like a working number.

After the fix: **20 of 23 schemas, 488 available, 428 never searched, 88%.**

### The control, and one thing still dead

`random_baseline` has no edge by construction. It is now in the lineup and **blocked on
six checks**. It returned a positive out-of-sample mean of +15.38 at p = 0.153 — which
is not a failure but the noise floor behaving as calibrated, caught six times over.

If the control ever clears, every number in this log is void. That check now runs on
every regeneration rather than living in a synthetic fixture.

Still dead: `variants_available` is computed in `phantom_service` and does not reach the
record. The gate's universe is still the explored count. Narrowed to two candidates and
not resolved.

### The last question: is any of this about crypto?

Every number here was measured on Binance. The 4h autocorrelation, the 1.19 bps spread,
the 7 bps cost, the 21.8% floor — properties of one venue.

MOEX, same estimators, five liquid names:

```
SBER  σ 2.13%  ρ1 +0.0046  t +0.24
GAZP  σ 2.12%  ρ1 +0.0142  t +0.28
LKOH  σ 1.89%  ρ1 −0.0089  t −0.31
GMKN  σ 8.48%  ρ1 −0.0149  t −8.54
ROSN  σ 2.09%  ρ1 +0.0567  t +1.72
```

Four liquid blue chips say nothing. The exception is GMKN — a stock that fell from 7029
to 118 on 8.5% daily sigma.

**That is not "markets are negatively autocorrelated".** It is the reading EDGE_ROLL
already implied for crypto: the negative covariance is bounce after a sharp move, and
it scales with how violent the series is. BTC at 4h has modest sigma and t = −3.4;
GMKN has 8.5% sigma and t = −8.5. Same mechanism, magnitude following the violence,
now with an independent venue supporting it.

**So the crypto searches do not transfer to equities as a general claim.** The ceiling
has to be argued per venue, and per regime within a venue.

And our criterion was wrong first: the first run reported 0 of 5 and was wrong on GMKN,
whose |ρ₁| sat *below* the `resolvable_phi` floor while |t| was 8.54 — impossible
together. `resolvable_phi` assumes the standard error behaves like 1/√n; true for
homoskedastic data, not for fat-tailed equity returns, where it was wrong by an order
of magnitude. Using it as the significance test discards exactly the series the direct
t-statistic finds most significant.

---

## The portfolio test, and the first diagnosis we got wrong

MOEX closed the crypto question by showing the effect was venue-specific. That left
one honest reason to keep looking: **maybe the finding is not in the market but in the
combination.** The paper's claim is about diversification — that a time-series momentum
rule applied across many instruments beats the same rule on one. Testing it on one
asset would test a different claim.

So: 15 survivorship-free ETFs, 2010–2026, registered parameters taken from the paper
and not tuned — 252-bar signal, 252-bar volatility window, 40% target, equal weight, the
product's own 7 bps/fill read from config rather than assumed. H1 beat random timing,
H2 beat its best member, H3 beat its average member.

The result came back 4/4 on H1 with a portfolio total of **−1.7005** and all fifteen
members negative. That contradiction was noticed immediately, and diagnosed as what it
superficially resembles: a relative test that a losing strategy can pass. "Beats random
timing" measures losing less than a neighbour, which is not the same as paying. We wrote
that down as rule 1 of the register, generically enough to apply, and moved on.

**That diagnosis was wrong about the cause, and the rule built on it was not sound.**

The re-run that recovered the numbers showed `portfolio` and `p90 of null` agreeing to
four decimal places in all four windows. A p90 from 40 permutations of a 749-bar window
does not reproduce that window's total four times running. The columns agreed because
the null could not differ from the thing it was testing:

```python
gross  = sum(legs)                      # permutation-invariant
active = sum(1 for x in legs if x != 0) # permutation-invariant
```

Shuffling the window reorders returns without changing either term. All 40 draws in a
window are the same number; p90 of a constant is that constant; `observed >= p90` holds
for **any** portfolio. A flat −50%-per-bar series also scores 4/4 — pinned now in
`tests/test_portfolio_null_degenerate.py`.

So H1 was never a relative test with a weakness. It was a test that could not fail, and
the relative-test rule was inferred from an instrument that had no variance to be
relative in. Had the numbers been copied from a commit message instead of re-derived —
which is exactly what they were about to be — the whole finding would have been
committed intact: a correct-sounding methodological warning, resting on nothing.

**What we did not do is redefine the null.** Replacing it after seeing that it degenerates
is the criterion-shopping this project registered against in the first place. A corrected
null has to resample *which bars are held*, so the cost term can vary, and it has to be
registered and run as its own hypothesis. It has not been written.

H2 and H3 never touched the null. Both fail: −1.7005 against a best member of −0.0479
and an equal-weight average of −1.1858. TSMOM as a portfolio lost money on this data and
diversification did not rescue it. That is a clean negative and it stands.

The generalisable lesson is not the one we first wrote down. It is the first rule in the
register now: **check that the null can vary before reading anything against it.** A
null invariant under the transformation it applies cannot reject anything, and its
agreement with the observed value looks exactly like a pass.

---

## The through-line

Six registered searches found nothing. Then we spent the rest of the effort finding out
**what it costs to say nothing was found**, and on the way discovered that three of our
own instruments were wrong, one of them rewrote the gate without saying so, and one
tool hid half the repository's search space by alphabetical accident.

Not one of the six verdicts changed. Almost everything underneath them did.

The portfolio test is the one that closes this out, and not in the way it was meant to.
It was run to find out whether diversification could rescue a negative result, and what
it found instead was a criterion that reported success for two sessions while being
incapable of reporting failure — alongside a real, standing negative about TSMOM on
equities that nobody had recorded.

The last finding was obtained by looking for a number a document should have contained
and did not. The gap was the defect's only remaining trace.