# Broker disconnect

Alert: `broker_disconnect` when `trading_broker_connected{broker=...}` is zero.

## Immediate response

1. Halt new risk and open an incident with account, broker, and UTC timestamps.
2. Do not retry non-idempotent submissions blindly. Treat timed-out orders as **unknown**, not rejected.
3. Query broker open orders, executions, positions, and cash through an independent session.
4. Match every unknown client/order ID before any resubmission.
5. Verify protective orders remain broker-resident.

## Recovery

Restore authentication/network access, then perform a full reconciliation. Import fills that occurred during the outage into the signal-to-reconciliation audit trace. Resume only after broker state is authoritative, duplicate suppression is healthy, and stop coverage is 100%.

## Escalation

For HTTP 503 or repeated timeouts, use bounded backoff and preserve idempotency keys. If protection cannot be verified, follow `emergency-halt-flatten.md` using the broker's approved manual channel.
