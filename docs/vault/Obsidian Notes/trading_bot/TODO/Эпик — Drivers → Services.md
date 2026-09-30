# Эпик — Drivers → Services

**Приоритет:** P1  
**Статус:** каркас universal adapter  
**Связано:** [[Архитектура — legacy (текущий код)]], [[Event Contract]], [[Universe selection]], docs/architecture/market_adapters.md

---

## Цель

Любая биржа → **адаптер** → единый Event Contract.  
`Drivers/*.py` → `services/*_market_service/` + `engine/adapters/` + `MarketDataPort`.

---

## Слои

- [x] `engine/market.py` — `MarketDataPort`, `candle_to_market_payload`, `BrokerExecutionPort`
- [x] `services/market_adapter_base.py` — follow_universe + publish
- [x] `MockMarketDataAdapter` + `mock_market_service`
- [x] `CcxtMarketDataAdapter` + `ccxt_market_service` (optional)
- [x] `QuikTransport` + `QuikMarketDataAdapter` + stub service (без QuikPy)
- [x] `Yahoo`/`Moex` adapters + `historical_data_service` → `market.history.candle`

---

## QUIK backend (следующий spike)

**Решение (spike):** preferred `quik_python`, alternate `qlua_rpc`, CI `fake`.  
См. `docs/architecture/quik_transport_spike.md`. QuikPy — legacy only.

- [x] Factory `build_quik_transport` + FakeQuikTransport + QuikBrokerExecution
- [x] Optional wrappers `quik_python` / `qlua_rpc`
- [x] Wire broker → `order_execution` / `TradingRuntime` (`mode=quik`)
- [x] Fake path E2E
- [ ] Live smoke на терминале

---

## Миграция legacy

| Legacy | Service / adapter | Статус |
|--------|-------------------|--------|
| TestDriver | mock | **сделано** |
| — | ccxt | **каркас** |
| Quik.py / QuikPy | quik + QuikTransport | **сделано** (fake E2E; live terminal TBD) |
| get_finance_data yahoo/moex | historical_data_service | **каркас** |
| metatrader / transaq | later | backlog |

---

## Критерии готовности (этот slice)

- [x] mock paper path на base без регрессии
- [x] смена биржи = новый адаптер, не правка стратегий
- [ ] live QUIK через выбранный transport
