# Fee or slippage mismatch

Alerts: `fee_mismatch` and `slippage_mismatch`.

## Triage

1. Halt new risk if the difference can invalidate sizing, drawdown, or account equity.
2. Export broker executions and fee lines without rounding or currency conversion.
3. Compare venue tier, maker/taker flag, instrument multiplier, fee currency, taxes, rebates, FX rate, and partial fills.
4. Compare expected arrival/decision price with actual fill timestamps for slippage.
5. Separate timing/accrual differences from model errors.

## Nightly primitive

Run `engine.observability.nightly_fee_reconciliation` from the approved job runner. It returns a report and inert `DraftAction` values. It never installs a schedule, halts trading, or executes a draft. An operator must review and activate any action through the audited command path.

## Resolution

Correct configuration prospectively, replay reconciliation, and attach before/after evidence. Do not rewrite historical fills. Resume only when equity, fees, and slippage are within approved tolerances.
