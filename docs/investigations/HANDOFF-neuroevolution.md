# Handoff prompt: finish the neuroevolution counting check

The work below was started and **did not complete** — my own bug, not a data limit.
Nothing was concluded. This file exists so the registration is not lost with the
unfinished code.

Copy the block into a fresh session with this repository open.

---

## THE PROMPT — copy from here

```
TASK
Finish a verification that was started and abandoned mid-implementation. Nothing was
concluded; do not treat any of it as settled.

READ FIRST
`docs/investigations/REGISTER.md` — the index of all 32 registered hypotheses with
verdicts (R-01…R-32), the 9 derived rules, and the correction log. The rules are
**preconditions for the next registration**, not lessons: rules 1 (check the null can
vary), 4 (report evaluations AND distinct candidates) and 5 (census the space before
searching it) apply directly to this task. Read them before touching code.

WHAT IS ALREADY ESTABLISHED
The neural-architecture search space for a small model over 6 features is finite and
enumerable. Enumeration gives exactly 50 architectures from:
    hidden units  (0, 2, 4, 8)        # 0 = no hidden layer, i.e. logistic
    depth         (0, 1, 2)
    activation    (none, tanh, relu)   # only reachable when depth > 0
    feature subset(0, 2)              # 0 = all features in training

50 is small and concrete. This matters and cuts against a common objection: an
architecture search does NOT necessarily explode the search space the way a
continuous parameter grid does, because topologies are discrete. The denominator is
obtainable by enumeration, exactly as the strategy-grid denominator was (727).

WHY THE CHECK EXISTS
A precondition was written down before any ML work: this project could not count its
own search space, and a model with a feature-subset dimension would multiply the thing
that was already wrong by ~11x. The universe was since fixed and recorded — 727
available against 64 explored — so the precondition is partly satisfied. What is NOT
yet established is whether an evolutionary search can be counted honestly, and whether
its answer survives out of sample. That is this task.

REGISTERED HYPOTHESES — fixed before running, do not revise them after seeing results
  H1  Distinct architectures are countable, and the count is materially below the
      evaluation count when populations overlap. Falsified if they are equal.
  H2  THE DECISIVE ONE. The architecture evolution selects does not survive
      out-of-sample, EVEN THOUGH a real signal is present and findable. Run it
      against two panels from the same generator: one with a real weak signal, one
      with identical variance and no signal. Against pure noise a search failing
      proves nothing; against a real signal, a search failing proves the search is
      the problem.
  H3  PBO over the evaluated architecture population is high, comparable to the 0.895
      measured on the strategy lineup. `engine.simulation.cscv_pbo` already exists and
      works on any set of candidate score series.

WHY THE SIGNAL MUST BE REAL AND FINDABLE
Include a full census pass — fit every one of the 50 architectures — before running
evolution. This establishes the fittest-by-construction and makes H2 interpretable.
Without the census, a failed search is indistinguishable from an unfindable signal.

DISCIPLINE — non-negotiable here
1. Pre-register. Criteria above are fixed. Record them before running, not after.
2. Publish BOTH counts: evaluations and distinct architectures. With overlapping
   populations they differ, and they answer different questions. Report only the
   larger one and the search looks bigger than it is; report only the smaller and
   effort is hidden.
3. No tuning of the optimiser to make evolution succeed. The question is whether the
   SEARCH can find a signal that is present. A weak optimiser would confound that with
   a bad implementation, so whatever search is used, the optimiser must be strong
   enough to fit a signal that is demonstrably there.
4. PBO is not optional. An evolutionary run without it is a fitness display.

WHAT WENT WRONG LAST TIME
The implementation died in a hand-written backprop: in the reverse pass `delta` was
reinitialised to `[err]` at the start of each layer instead of being carried backward,
so gradient indices went out of range (`IndexError: list index out of range` on
`dz[u]`). The forward pass was fine.

Start from there. Either fix it — note that for the output layer the error has size 1
and for a hidden layer it must have that layer's width — or replace it with a
numerically stable primitive (finite differences, or numpy if it is available). Do not
hand-roll more than necessary: the deliverable is the counting result, not the trainer.

THE FAILURE MODE THIS SESSION EXISTS TO AVOID
At least six bugs in this repository were plausible-looking numbers that were wrong:
a bootstrap p-value of 0.975 for a mean of 3.0 with a tight interval excluding zero; a
cost model reporting +120% on a zero-autocorrelation series; a denoiser crediting +901
bps at p95 to a strategy with zero trades; a Reality Check passing at 4/4 windows
while the portfolio lost money in all of them; and a portfolio null that printed 4/4
with its two columns agreeing to four decimal places in every window — because the
null was built by permuting the window while the statistic compared depended only on
the window's sum and non-zero count, both permutation-invariant. All 40 draws were one
number; the comparison held for ANY portfolio, including a flat -50%-per-bar series.
Every one was caught by a KNOWN-ANSWER test — a series whose correct output is not in
doubt.

The sixth one carries an extra warning. It was found only because the register
required numbers that no document contained, which forced a RE-RUN of the tool instead
of citing a summary. Had the numbers been copied from a commit message, the finding
would have been committed intact: a correct-sounding methodological warning about a
test that could not reject anything. So: re-derive, do not cite. And when this task
produces its numbers, write them into `raw-record.md` and `REGISTER.md` immediately
(rule 9) — R-19's numbers lived for two sessions in a commit message and a docstring,
and that gap was the only reason the defect was still findable.

So: before reporting anything from this instrument, run it on a case whose answer is
known. Fit a pure-noise panel and a pure-signal panel; a broken trainer produces
plausible scores on both, and a real one does not. AND: print the spread of the null
before reading any comparison against it. Agreement between the observed value and the
null is indistinguishable from a pass when the null cannot vary — the table agreeing
to four decimal places WAS the defect (rule 1).

DO NOT
- Present fitness, loss or accuracy as a result without an out-of-sample number and a
  denominator next to it.
- Treat a negative outcome as a reason to enlarge the network, the population or the
  epochs. That is best-of-N by construction and was declined in advance here for a
  reason that does not expire when the numbers come in badly.
- Describe this as settled if it is not. The registration exists precisely so that the
  outcome is binding rather than negotiable.

DELIVERABLE
1. The three hypotheses answered, with the counts reported beside them.
2. A known-answer check on the trainer, run first and shown.
3. If H2 holds — evolution cannot find a signal that is present — that is the useful
   result and it should be written up as a finding, not treated as a failed build. It
   settles the ML lead negatively with evidence, which is worth more than a plugin
   that cannot be shown to work.
```

---

## Notes for whoever picks this up

**The registration is the asset here.** The three hypotheses, the census requirement
and the two-count rule are all that survived the attempt. Re-deriving them would take
longer than running the check.

**The census is what makes H2 mean anything.** It is easy to run evolution, get a bad
pick, and conclude nothing — because a search failing on noise proves nothing about
searches. Running all 50 architectures first establishes that a good answer exists in
the space. Only then does the evolutionary run failing say something about evolution.

**The known-answer check is not optional ceremony.** Every expensive wrong answer in
this project's history came from an instrument nobody validated against a case whose
answer they already knew. The trainer in particular is hand-written, which is exactly
the condition under which that happens.

**What this decides.** If H2 holds, the ML lead is closed negatively with evidence
rather than deferred — which is a better outcome than a plugin, because a plugin
whose fitness curve looks good is precisely the thing this project has been refusing
to build for two days.

**Status at handoff.** 32 hypotheses registered; error catalogue 19 entries this
session (23 parallel, 5 of those verified closed here — the catalogues are not
additive). No hypothesis has returned a tradable edge. R-19 is `NOT EVALUABLE` with
its 4/4 withdrawn, NOT counted as pass or failure — withdrawn results stay in the
record with their reason (rule 7). If this task produces a verdict, it enters
`REGISTER.md` as R-33 or R-34 with the same vocabulary, and any correction to this
file's own claims goes in that file's correction log, not as a silent edit.