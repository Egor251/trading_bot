# Эпик — Universe selection

**Приоритет:** P0 (фундамент live-данных)  
**Статус:** сделано (каркас) / follow-up open  
**Канон:** `docs/architecture/universe_selection.md` · [[Universe selection]]  
**Связано:** [[DATA FLOW]], [[Event Contract]], [[Эпик — Drivers → Services]], [[Эпик — источники данных]]

---

## Цель

Ответить продуктово и в коде на вопрос: *чем торговать из тысяч инструментов?*  
Не live-мониторить всё; воронка catalog → candidates → active; market services стримят только active.

**Философия:** максимальная настраиваемость + максимальная адаптивность — и на этом слое  
(профили screen, не зашитый top-N; смена критерия / union books). Канон: [[Философия архитектуры]].

---

## Сделано

- [x] Канон-док `docs/architecture/universe_selection.md` + vault
- [x] `engine/universe.py` — профили (`liquid` / `volatility` / `futures_speculation` / …), rank ≠ floor
- [x] `services/universe_service` — `universe.snapshot` / `universe.changed`
- [x] `config/universe_catalog.yaml` + блок `universe:` в `trading_bot.yaml`
- [x] `mock_market_service.follow_universe`
- [x] Вшивка в philosophy, техдок, DATA FLOW, меморандум
- [x] Unit tests (в т.ч. vol rank, futures profile, merge books)

---

## Follow-up

- [ ] Pin инструментов с открытой позицией
- [ ] Multi-profile union в `universe_service` (консилиум: trend+vol+options)
- [ ] Catalog feed с биржи + расчёт atr_pct / iv_rank
- [ ] Options **chain** screener (DTE, delta, OI, spread) — отдельно от underlying
- [ ] `quik_market_service` / CCXT — subscribe только active
- [ ] REPL: `show universe`, смена profile
- [ ] UI: active / candidates / profile

---

## Критерии готовности (каркас)

- Reselect меняет active → mock публикует свечи только по новому набору
- Стратегии не хардкодят полный рынок; потребляют `market.candle` с active
