# Emergency halt and flatten

Use for uncontrolled drawdown, lost protection, duplicate orders, corrupt state, or a broker/feed incident that makes continued trading unsafe.

## Halt

1. Announce incident ID, account, scope, operator, and UTC time.
2. Issue the audited **halt new risk** command. Verify the risk gate rejects risk-increasing orders.
3. Cancel only working entry orders. Preserve protection until replacement or flatten is confirmed.
4. Obtain broker-authoritative positions and open orders.

## Flatten (separate approval)

Flatten is not implied by halt. A second operator approves the instrument list, quantities, venue, order type, and maximum slippage. Reduce positions incrementally when liquidity is impaired. Verify each fill at the broker before submitting the next revision. Cancel remaining protective orders only after the corresponding position is flat.

## Exit criteria

Broker positions are flat (or explicitly accepted exceptions), no unknown working orders remain, cash and fees reconcile, and the complete command/order/fill timeline is retained.

Do not resume during the incident. Recovery uses `restore.md` and a separately approved resume command.
