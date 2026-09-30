# Эпик — Event Contract

**Приоритет:** P0  
**Статус:** v0.1  
**Связано:** [[DATA FLOW]], [[Universe selection]]

---

## Цель

Единый контракт событий `universe.*`, `market.*`, `strategy.*`, `execution.*` — все сервисы и плагины публикуют/подписываются только через него.

---

## Задачи

- [x] Спека `universe.snapshot` / `universe.changed` в [[Event Contract]]
- [x] Утвердить [[Event Contract]] v0.1
- [x] Typed helpers: `host/events/` (normalize + publish_*)
- [x] `publish_market_candle` / `publish_strategy_signal` / `publish_execution_result`
- [x] Legacy mapping в Event Contract
- [x] REPL: `show storage`, `show universe`, `halt` / `resume`

---

## Критерии готовности

- [x] mock service публикует через normalize helpers
- [x] test_strategy / ma_cross_example фильтруют по `ticker`
- [x] market services реагируют на `universe.changed` (active book)
