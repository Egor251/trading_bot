# Prompt: how to produce this kind of investigation journal

Copy the block below into a fresh session, attach the raw dialogue, and it should
produce the same three-layer artifact. The notes after the block explain what each
part is for and why the failure list is the part that matters.

---

## THE PROMPT — copy from here

```
You are reconstructing an investigation log from a conversation transcript.

CONTEXT
The transcript is of a quantitative research session in which a trading system was
built and a set of registered experiments was run to test whether any strategy had
positive expectancy after costs. Most results were negative. The value of the log is
NOT the results — it is the record of how the reasoning moved and how the instruments
were repeatedly wrong.

YOUR TASK
Produce three files, in order, from the transcript only. Do not add anything you
cannot point at in the transcript. Where you are inferring, say so.

=== FILE 1: log.md — the summary ===
Registered tests, criteria, results, verdicts. Tables. What was measured.
Include a provenance table stating, per period, whether you are reading the
transcript directly or reconstructing from a summary someone else wrote.

=== FILE 2: narrative.md — the reasoning chain ===
Built around DECISION MOMENTS, not results. The shape of each entry:
  - what was observed
  - what hypothesis it suggested
  - what that hypothesis would predict
  - what actually happened
  - therefore what was decided next, and what was ruled out
Critical: include the moments where nothing moved. Six consecutive negative results
that did not change anyone's mind is more informative than a list of the results.
Also include moments where a confident answer was later withdrawn.
Mark clearly anywhere you are reconstructing reasoning that the transcript only
summarises. Decisions survive a summary; hesitations do not. Do not invent them.

=== FILE 3: raw-record.md — the data ===
Every registration with its criterion as fixed in advance. Every measurement with
its actual output, including runs that were wrong. Numbers exactly, not rounded into
smoothness. If a run printed something absurd, print it absurd — that is often how it
was caught.

DISCIPLINE RULES — these are the point, not the formatting
1. Pre-registration. Every test records the criterion that was fixed BEFORE data was
   seen. If the transcript shows a criterion being changed after seeing results, that
   is a finding: record it, and record whether it was corrected toward the registered
   form or the reverse.
2. Multiplicity. Any search over more than one variant is corrected. Record the
   denominator AND whether it is the number of variants explored or the number
   available. These differ enormously and conflating them is the single most common
   error in this domain.
3. Corrections are first-class. When a claim is withdrawn, record the original claim,
   the corrected one, and what the error was. Do not silently amend.
4. A negative result is evidence about a method, not about a market. Record which
   one every negative is.
5. Known gaps get named with the reason, not worked around silently.

ERROR CATALOGUE — extract every error you can find, and for each record HOW IT WAS
CAUGHT, not just that it existed. This index is the most valuable part of the
document. Check specifically for these, which is where this domain actually fails:
  - a quantity recorded in one place and interpreted in another (fees vs slippage vs
    impact; variants explored vs variants tried; per-fill vs per-trade units)
  - a bootstrap distribution used without the null hypothesis inside the resample
    (asking where the observed mean falls inside its own bootstrap gives ~1 always,
    because a bootstrap distribution is centred on the observed statistic)
  - a significance test used where a resolution/sample-size floor was needed, or the
    reverse — check whether the two disagree on any series and report the
    disagreement rather than picking the convenient one
  - a threshold applied with the wrong sign (a positive bar against a negative
    statistic always passes)
  - an operation that is a no-op but looks like a safeguard (trimming the worst items
    from a maximum; a filter that removes nothing)
  - an optional/fallback code path silently returning a default that happens to equal
    the expected value, so every test passes while the wiring is dead
  - a research or measurement tool that mutates production state as a side effect
  - a configuration placeholder (an empty dict, a None) accepted downstream as if it
    were a real value
  - a lookup that returns different results on different passes — if the transcript
    shows two passes disagreeing over the same data, that is a finding about the
    reader, and the cause is usually iteration order or an early exit that stops on a
    base class or a placeholder
  - a cost total reset inside a loop, so net equals gross

STYLE
- Prefer the specific number over the adjective.
- When a result contradicts a stated assumption, say so loudly. A measurement that
  contradicts an assumption is information; a criterion that quietly absorbs the
  contradiction is a defect.
- State what the log does NOT claim, in its own section.
- End with what was still open when the log was written.
- Never reconstruct verbatim dialogue you do not have. A summary gives you
  decisions and numbers; it does not give you hesitations, and writing them anyway
  defeats the purpose of the document.

If you catch yourself making a claim about the transcript that you cannot point at,
mark it as inference and continue. Marking is free; being wrong is not.
```

---

## Notes on the prompt

**Why three files rather than one.** The summary answers "what was measured", the
narrative answers "why that came next", and the raw record answers "show me". A
reader who distrusts a claim can go straight to the numbers; a reader who wants the
reasoning gets the reasoning without the tables in the way.

**Why the error catalogue extraction is mandated rather than suggested.** The
headline result of a session like this is nearly always "nothing was found", which is
the least publishable and least useful outcome in the domain. What has actual value is
the catalogue, and specifically the **"how it was caught"** column — because that is
what lets a reader transfer the lesson rather than admire the number. A catalogue that
only lists errors teaches nothing transferable.

**Why the failure list is specific.** Every item in it is a real failure mode from this
project, not a generic checklist. An abstract prompt would get generic work; the
bootstrap-centred-on-the-observed-statistic trap and the placeholder-base-class trap
are the two that produced wrong conclusions here, and both are the kind of thing a
model will otherwise write without warning.

**Why "mark it as inference and continue".** A model asked to produce a research log
will otherwise smooth over gaps to make the document read better. Explicit permission
to mark is what keeps the gaps visible. The prompt states that marking is free and
being wrong is not, because that is the asymmetry that actually governs behaviour.

**Why the no-claim section is required.** A log of failures invites the reader to
over-generalise from it. "Markets are unpredictable" is not what was shown, and the
document has to say so itself or a reader will supply it.

---

## Using it on the missing session

Feed it the raw first session and it will produce the `[S1]` half of all three files.
What it will produce from a summary rather than a transcript is already marked in the
existing documents as second-hand, so the two halves will be directly comparable.

Two things worth adding to the attachment if you have them:

1. **The criteria as originally written**, before results. If the transcript only
   contains criteria that were quoted afterwards, they are reconstructions and the
   prompt will mark them so.
2. **Any output that looked wrong at the time.** Those are the highest-value inputs
   and they are the easiest to omit from a pasted transcript, because at the moment
   they were happening they did not yet look like findings.