# Event Contract

**Статус:** v0.1 утверждён (2026-07)  
**Связано:** [[DATA FLOW]], [[Архитектура — целевая (microkernel)]]

> Канонический формат: `KernelEvent` из microkernel_core (`event_type`, `source_id`, `payload`, `event_id`, `timestamp`, `instance_id`).  
> Typed helpers: `host/events/` (`normalize_*`, `publish_market_candle`, `publish_strategy_signal`, …).  
> Поле `schema_version: 1` добавляется нормализаторами.

---

## Принципы именования

```
<domain>.<entity>[.<qualifier>]
```

| Domain | Publisher | Subscriber |
|--------|-----------|------------|
| `market` | market services | strategies, analytics, REPL |
| `universe` | universe_service | market services, strategies, UI |
| `strategy` | strategy plugins | RuleEngine, REPL |
| `execution` | order plugin, services | RuleEngine, REPL, analytics |
| `system` | core, REPL | logging, UI |
| `backtest` | backtest plugin | optimizer |

Подписка **точная** (`market.candle`) или **широкая** (RuleEngine: `*`).

---

## market.*

### `market.candle`

Источник: `quik_market_service`, `mock_market_service`, replay from `historical_data_service`.

```json
{
  "class_code": "TQBR",
  "ticker": "SBER",
  "interval_min": 1,
  "date": "2026-07-06",
  "time": "14:35:00",
  "open": 285.10,
  "high": 285.50,
  "low": 284.90,
  "close": 285.30,
  "volume": 125000
}
```

### `market.dom`

```json
{
  "class_code": "TQBR",
  "ticker": "SBER",
  "bids": [{"price": 285.0, "quantity": 1000}],
  "asks": [{"price": 285.1, "quantity": 500}]
}
```

### `market.portfolio`

```json
{
  "positions": [
    {"class_code": "TQBR", "ticker": "SBER", "quantity": 100, "avg_price": 280.0}
  ],
  "orders": [
    {"id": "123", "ticker": "SBER", "side": "BUY", "quantity": 10, "price": 285.0, "status": "active"}
  ],
  "cash": 150000.0,
  "currency": "RUB"
}
```

### `market.order_update`

```json
{
  "order_id": "123",
  "ticker": "SBER",
  "status": "filled",
  "filled_qty": 10,
  "filled_price": 285.05
}
```

### `market.history.candle`

Batch/historical — те же поля что `market.candle` + `source: "yahoo"|"moex"|"alphavantage"`.

---

## universe.*

Источник: `universe_service`. Market services стримят **только** `active`.

### `universe.snapshot`

Полный снимок после каждого reselect (catalog/candidates/active + counts).

```json
{
  "mode": "screener",
  "profile": "volatility",
  "rank_by": "atr_pct",
  "reason": "profile:volatility:rank:atr_pct:timer",
  "pass_reason": "timer",
  "counts": {"catalog": 8000, "candidates": 100, "active": 20},
  "active_keys": ["TQBR:GAZP", "TQBR:SBER"],
  "active": [{"symbol": "GAZP", "class_code": "TQBR", "atr_pct": 0.04, "dollar_volume": 5e8, "key": "TQBR:GAZP"}],
  "candidates": []
}
```

Профили отбора (`liquid` / `volatility` / `futures_speculation` / …) — см. [[Universe selection]]. Ликвидность — пол (floor) и/или ранг, не единственный критерий.
### `universe.changed`

Только когда изменился набор `active_keys` (подписка market adapters).

```json
{
  "added": ["TQBR:LKOH"],
  "removed": ["TQBR:GAZP"],
  "active_keys": ["TQBR:SBER", "TQBR:LKOH"],
  "active": [],
  "mode": "screener",
  "pass_reason": "timer"
}
```

---

## strategy.*

### `strategy.signal`

Источник: strategy plugins.

```json
{
  "strategy_id": "test_strategy",
  "action": "BUY",
  "class_code": "TQBR",
  "ticker": "SBER",
  "quantity": 10,
  "order_type": "limit",
  "price": 285.0,
  "reason": "macd_cross",
  "params_snapshot": {"fast": 12, "slow": 26}
}
```

`action`: `BUY` | `SELL` | `CLOSE` | `HOLD` (informational).

### `strategy.state`

Optional — heartbeat / debug state export for REPL.

---

## execution.*

### `execution.request`

Источник: order_execution plugin (или RuleEngine action).

```json
{
  "broker": "quik",
  "transaction": { "...": "QUIK-specific, normalized where possible" }
}
```

### `execution.result`

```json
{
  "success": true,
  "order_id": "12345",
  "error": null,
  "broker_reply": {}
}
```

---

## system.*

### `system.halt` / `system.resume`

Операторский kill switch (TradingBotCore / UI / REPL).

```json
{ "reason": "kill_switch" }
```

Runtime публикует `system.halted` / `system.resumed`. При halt `order_execution` отклоняет новые заявки.

### `system.repl.command`

REPL → ядро (future): operator commands as events.

```json
{
  "command": "show",
  "args": ["strategy"]
}
```

---

## backtest.*

### `backtest.run_started` / `backtest.run_finished`

```json
{
  "run_id": "uuid",
  "strategy_id": "test_strategy",
  "params": {"a": 5, "b": 10},
  "profit": 1234.56
}
```

---

## Legacy → Event mapping

| Legacy call | Target event |
|-------------|--------------|
| `Quik.get_candles()` | `market.candle` |
| `Quik.get_DOM()` | `market.dom` |
| `Quik.get_portfolio()` | `market.portfolio` |
| `Quik.set_transaction()` | `execution.request` → service |
| `Strategy.run()` signal | `strategy.signal` |
| `optimizer` iteration | `backtest.run_*` |

---

## Versioning

- v0.1 — черновик, breaking changes allowed
- При стабилизации: поле `payload.schema_version: 1` или prefix `market.candle.v1`

---

## Open questions

- [x] Universe: live на весь рынок vs whitelist? → **воронка catalog → active** ([[Universe selection]])
- [ ] Topic per ticker: `market.candle.TQBR.SBER` vs filter in handler?
- [ ] Rate limiting / backpressure на шине (50k queue из yaml)
- [ ] Paper vs live flag in payload or instance config?
- [ ] Pin open positions в active при выпадении из screener
