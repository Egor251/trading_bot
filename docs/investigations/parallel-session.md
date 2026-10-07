# Parallel session record, and one verified conflict

Material from a second session working the same repository. Kept separate because it
is a **different conversation** with its own provenance, its own direct/second-hand
split, and its own errors — several of which this session has no record of at all.

That session had no filesystem access and could not write these files itself. They
arrived as pasted text.

## Where the two records overlap, and they agree

| quantity | this session | parallel session |
|---|---|---|
| null calibration, independent | 12.3% (2071/16800) | 12.3% (2071/16800) |
| null calibration, market factor | 19.5% (3280/16800) | 19.5% (3280/16800) |
| null calibration, serial | 22.0% (3696/16800) | 22.0% (3696/16800) |
| p90 windows ≥ floor | floor must be ≥10 windows | floor must be ≥10 windows |

Identical to the run. Same tool, same seeds, same arithmetic — which is the one piece
of external corroboration in the whole record, and it matters: it says the null
calibration is reproducible.

## Where they conflict, and the code settles it

The parallel session lists as **open**:

> `_demean` defect — fix specified, not implemented. Denoiser subtracts median,
> assuming unit exposure; `volatility_breakout_example` has zero trades and zero
> exposure; it received `0 − (−901.76) = +901.76`, p95, for not participating in a
> decline.

**This is already fixed in the working tree.** `tools/window_stability.py:718`:

```python
def _demean(..., exposure: dict[str, list[float]], ...):
    """Strip the per-window market factor out of every strategy, by exposure."""
    residual = net - (exposure_i / sum_exposure) * sum_net
```

and the docstring names the defect, the wrong numbers, and the reasoning:

> The previous version subtracted the median across strategies, which silently
> assumed every strategy was exposed to the market equally — exposure 1 for all. A
> strategy whose exposure is 0 and whose net is 0 had median subtraction hand it
> `0 - median`: a market position it never held. Measured on the 3000-bar BTC …
> up to 5319 of apparent [P&L].

Those figures — `−934.94 / +901.76 / −5319.45 / +188.87` — are the parallel session's
E19 numbers verbatim. **The fix is in the code, and the code documents why.**

So the parallel session ran against an **earlier state of the tree**. Its "open" list
is stale on this point, and its E19 is a fixed defect recorded as found.

**This is the useful part of the conflict:** two independent records of the same defect
— one as an open bug, one as a closed one — and the code is what resolves it. Neither
record could have settled it alone. A single-session log would have carried whichever
version that session happened to see.

## What the parallel session has that this one does not

Its error catalogue is **23 entries, several genuinely new**:

- **E4** — correlation dampening skipped in `equal` mode, which is the *shipped
  default*. Five strategies at ρ = 0.95 each traded full size. A threshold not applied
  on the path actually in use.
- **E14** — `disk_cache_dir` resolved against CWD, so a research run wrote into the
  repository's `data/ohlc_cache/`. Caught by `tests/conftest.py` failing the suite.
  *This session's equivalent is §11 of `raw-record.md`: a research tool writing to the
  live gate. Same class, and it recurred.*
- **E12** — 19 bystander plugins active on a shared runtime; three strategies measured
  alone gave +12.37 / −17.13 / +10.41, and in the full run −7.45 / −8.48 / −9.01 —
  **agreeing to the cent.** A safeguard that removed nothing.
- **E6** — a conclusion built on 4 null seeds (+252.64) stated as fact, wrong at 20
  seeds (+2.23). The rule "four seeds is a floor, not a measurement" was applied to
  other people's results and not to its own author's conclusion.
- **E15** — `TIMEFRAME.endswith("h")` made 4h claim ~1998 bars for a 1000-bar window,
  and that number became the fetch limit.
- **E17** — `evaluate_at` signature reversed relative to the call site.

Its **M3** is the strongest single result in either session and needs no
corroboration: 400-bar and 1000-bar sweeps on identical data give
**Spearman ρ = −0.337** (exact permutation p = 0.159), 12 of 19 signs flip, and the
short window's top five **lose** money on the long window while its bottom five **make**
it. A ranking is not a property of a strategy.

## The parallel session's own closing judgement, and it is right

It flags two things as worth checking before accepting its work. Both hold up:

**Its E18/E21/E22 are not separate from the multiplicity thread.** `variants_count`
overwritten per run, so 30 evaluations read as 1 — that is a 30× deflation failure, not
a bookkeeping detail. This session found the same family independently via
`variants_tried_lifetime` (recorded in its store as "the denominator a deflation
argument needs", read by nobody) and via `universe_basis`. Three sessions, one bug.

**Its E13 and E19 are two versions of the same failure, six weeks of code apart.** One
at the ledger level — `trades` from the per-run fill ledger, `pnl` from a runtime
equity that survived every run — and one at the statistics level, the median denoiser.
Both hand a **positive** result to a strategy that did not trade. Its conclusion is
right and it is the most transferable single sentence in either record:

> A check of "did it trade at all" would have cost five minutes at the start and
> caught both.

## What neither session has verified

The parallel session explicitly marks three things as **not found in its dialogue**,
and is right to: the bootstrap-centring error, the cost-total reset inside a loop, and
the positive-bar-against-a-negative-statistic sign error. Those are from *this*
session's transcript, present in `raw-record.md` §S2-2 and the catalogue as errors 2,
4 and 5. The two catalogues are **disjoint** where they could have overlapped, which is
itself the finding: the same repository, the same discipline, two sessions, and not one
error in common.

Nothing was found and nothing was fixed twice. Which is either evidence that the checks
work, or evidence that nobody looked at the other's work.

## Still open, across both records

1. **`variants_available` does not reach the record** — this session, unresolved.
2. **`disk_cache_dir` against CWD** — parallel session's E14, status in this tree
   unverified.
3. **A config knob shipped under the wrong section** — parallel session's E21; this
   session added three such knobs (`require_multiplicity_correction`, `require_batch_
   verdict`, `universe_basis`) and pinned all of them with a config test *after* being
   bitten by exactly this class.
4. **The diversified-trend-portfolio hypothesis** — parallel session's D10. The paper's
   result is a 58-instrument, volatility-scaled portfolio; only the single-asset version
   was tested. Still untested.
5. **A five-minute guard that would have caught two independent bugs** — see above.