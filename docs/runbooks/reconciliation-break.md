# Reconciliation break

Alert: `reconciliation_break` when `trading_reconciliation_ok{account=...}` is zero.

## Immediate response

1. Halt new risk for the affected account; leave valid protective orders active.
2. Capture immutable engine and broker snapshots: positions, cash, open orders, fills, fees, and timestamps.
3. Classify the difference as timing, missing fill, duplicate order, symbol mapping, quantity, price, cash, or fee.
4. Trace the execution through signal → risk → order revisions → fills → costs → protection → reconciliation.
5. Never overwrite local state merely to make totals match.

## Repair and validation

Use broker executions as the authority for traded quantity, then apply a reviewed correction event. Re-run reconciliation from the previous known-good checkpoint. Require zero unexplained position differences, expected cash/fee tolerances, no unknown orders, and full stop coverage.

Resume requires two-person review for live accounts and an audited command linked to the incident.
