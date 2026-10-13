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
At least five bugs in this repository were plausible-looking numbers that were wrong:
a bootstrap p-value of 0.975 for a mean of 3.0 with a tight interval excluding zero; a
cost model reporting +120% on a zero-autocorrelation series; a denoiser crediting +901
bps at p95 to a strategy with zero trades; a Reality Check passing at 4/4 windows
while the portfolio lost money in all of them. Every one was caught by a KNOWN-ANSWER
test — a series whose correct output is not in doubt.

So: before reporting anything from this instrument, run it on a case whose answer is
known. Fit a pure-noise panel and a pure-signal panel; a broken trainer produces
plausible scores on both, and a real one does not.

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