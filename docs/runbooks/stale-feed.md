# Stale market feed

Alert: `stale_feed` when `trading_feed_last_seen_seconds` is older than the configured limit.

## Immediate response

1. Acknowledge the alert and record an incident/trace ID.
2. Halt **new risk**. Do not cancel protective orders merely because market data is stale.
3. Compare exchange time, host UTC time, and the last accepted event timestamp.
4. Check adapter connectivity, subscription count, rate limits, and symbol/session status.
5. Confirm staleness from an independent market source before changing positions.

## Recovery

Restart only the affected adapter. Require a monotonic sequence of fresh events and valid bid/ask or candle data for two normal feed intervals. Reconcile positions and open orders before resuming. Record halt and resume as audited operator commands.

## Escalation

If exposure is unprotected or prices cannot be independently established, follow `emergency-halt-flatten.md`. Never flatten from a stale mark without broker-side confirmation.
