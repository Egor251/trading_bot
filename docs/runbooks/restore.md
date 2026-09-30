# Engine and UI restore

This runbook covers host failure, engine restart, UI restart, and restoration from durable state.

## Preserve and isolate

1. Halt new risk at the broker/manual control plane when engine state is uncertain.
2. Save logs, audit JSONL, configuration hash, build/commit ID, health snapshot, and the last known reconciliation.
3. Verify backup integrity before replacing any files. Never restore secrets from repository artifacts.

## Restore order

1. Restore durable audit/state to a clean instance.
2. Start the engine disconnected from order submission.
3. Load broker-authoritative fills, positions, cash, and open orders since the checkpoint.
4. Rebuild positions and costs, then reconcile.
5. Verify fresh market data, broker connectivity, no unknown or duplicate orders, 100% stop coverage, and acceptable drawdown/cost metrics.
6. Start or reconnect the UI last. A UI restart must not alter engine generation or trading state.

## Resume

Run smoke/read-only checks, archive the recovery evidence, and obtain operator approval. Resume with an audited command tied to the incident. Monitor every alert through at least one normal reconciliation cycle.

Rollback if any sequence gap, unmatched order, stale feed, or state divergence remains.
