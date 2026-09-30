# QUIK transport — решение

**Дата:** 2026-07-25  
**Статус:** каркас закрыт (live-терминал — отдельный smoke)

---

## Решение

| Роль | Backend | Почему |
|------|---------|--------|
| **Preferred live** | `quik_python` (Alex-Shur / PyPI `quik-python`) | native async, `get_last_candles`, не singleton QuikPy |
| **Alternate live** | `qlua_rpc` (`qlua` + Enfernuz/quik-lua-rpc) | явный ZMQ/protobuf сервис в QUIK |
| **Dev / CI** | `fake` | детерминированные свечи + транзакции |
| **Not configured** | `null` | default в YAML |
| **Legacy only** | cia76 QuikPy | тег `legacy-v1` — не канон |

Код: `engine/quik_transports/`, `engine/quik_broker.py`, `plugins/market/quik_market_service`, `TradingRuntime.submit_signal_async`.

---

## Путь исполнения

```text
strategy.signal → rule → order_execution
  → TradingRuntime (mode=quik)
  → risk check → QuikBrokerExecution → QuikTransport.send_transaction
  → execution.result
```

- `trading.mode: paper` — как раньше (синтетический fill)
- `trading.mode: quik|live` — нужен `quik_market_service.transport` ≠ `null`

---

## Конфиг (dry-run без терминала)

```yaml
trading:
  mode: quik

services:
  mock_market_service:
    enabled: false
  quik_market_service:
    enabled: true
    transport: fake
    transport_options:
      base_prices: { SBER: 285.0 }
```

Live:

```yaml
quik_market_service:
  enabled: true
  transport: quik_python
  transport_options:
    host: 127.0.0.1
    requests_port: 34130
    callbacks_port: 34131
  account: ""
```

REPL: `show quik`

---

## Чеклист

- [x] Factory `null|fake|quik_python|qlua_rpc`
- [x] `QuikBrokerExecution` → `order_execution` / runtime
- [x] Fake E2E (`tests/test_quik_path_e2e.py`)
- [ ] Реальный smoke на терминале QUIK
- [ ] Push/subscribe new candle (сейчас poll)
- [ ] Listings через QUIK transport (есть MOEX ISS)
