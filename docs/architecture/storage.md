# Хранение данных (канон)

**Статус:** канон  
**Эталон UX:** easm-gate-guardian Dashboard → Storage (тонкий слой)  
**Примитив:** `Code_base/cb_storage` (`StorageKit`, hot `kit.reroute`)  
**Операторская панель:** Система → Хранение

---

## Принцип

**Файлы — исключение:** boot YAML + human-edit (XML rules).  
**SoT:** SQLite / DuckDB / Postgres / ClickHouse через `cb_storage`.  
JSON/JSONL — только spill/export.

Миграция legacy JSON→БД на этапе разработки **не требуется**.

---

## Конвейер сигналов

```text
strategy.signal     →  голос стратегии (intent)
        ↓
ensemble.decision   →  консилиум принял в оборот
ensemble.reject     →  не принял (flat / не в lineup / zero weight / skip direct)
        ↓
execution.result    →  fill или отказ исполнения
risk.reject         →  risk/insurance/halt / rejected order
        ↓
portfolio.snapshot  →  equity + позиции (throttled / после fill)
```

| Слой | Событие | Зачем |
|------|---------|--------|
| Сигнал | `strategy.signal` | Что предложила стратегия |
| Решение | `ensemble.decision` | Что пошло в оборот |
| Отказ консилиума | `ensemble.reject` | Почему не пошло (flat, not_in_lineup, …) |
| Исполнение | `execution.result` | Fill / reject факта заявки |
| Risk | `risk.reject` | Insurance, position policy, halt |
| Портфель | `portfolio.snapshot` | Equity/позиции во времени |
| Lineup | `config.lineup_changed` | Кто был в консилиуме и с какими весами |
| Юниверс | `universe.changed` / `universe.snapshot` | Чем торговали |
| Ops | `ops.alert`, `system.halt*` | Инциденты и kill-switch |
| Reconciliation | `reconciliation.incident` | Расхождение с брокером |
| Costs | `cost.schedule_activated` | Какой cost schedule активен |
| AI | `ai.analyst.request` / `response` | Трассы ассистента |
| Research | `phantom.*`, `backtest.*` | Shadow / оптимизация |

**Observe/shadow:** режим стратегии в ростере (`trading.ensemble.observe` + `plugins.<id>.observe_only`).  
Стратегия **running**, пишет `strategy.signal` с `observe_only=true`, **не** входит в aggregate консилиума и **не** исполняется. Phantom при capture может строить теневой PnL. Режим `off` — плагин остановлен и молчит.

---

## Durable subscribe (`storage_service`)

Пишутся все топики из `_SUBSCRIBE` в `services/storage_service` (сигналы, rejects, portfolio, lineup, ops, recon, AI, phantom/backtest, market).

---

## Routing (классы kit)

| Класс | UI-имя | Типичные события | Backend |
|-------|--------|------------------|---------|
| `finding` | **Сигналы** | `strategy.*` | sqlite |
| `audit` | Исполнение и аудит | `execution.*`, `system.*`, `ensemble.*`, `risk.*`, `ops.*`, `reconciliation.*`, `portfolio.*`, `config.*` | sqlite / postgres |
| `state` | Юниверс | `universe.*` | sqlite / postgres |
| `semantic_stream` | Рыночный поток | `market.*`, phantom/ai/backtest (fallback) | sqlite → duckdb |

Внутренний id `finding` — совместимость с `cb_storage`; в UI — **«Сигналы»**.

`cb_storage.classify` maps `ensemble.*` / `risk.*` / `ops.*` / `reconciliation.*` /
`portfolio.*` / `config.*` / `cost.*` → **audit**; `phantom.*` / `backtest.*` / `ai.*` →
**semantic_stream**.

---

## Hot path vs durable

- **Hot:** `CandleStore` / DOM в памяти.  
- **Durable:** async `StorageSink` (не блокировать EventBus).  
- **Portfolio:** throttle (~60s) + force после успешного `execution.result`.

---

## Runtime-настройка

Панель **Система → Хранение**: hot `kit.reroute`, optional persist в YAML.

---

## Связанное

- [philosophy.md](philosophy.md)  
- `host/events` — publish helpers  
- `services/storage_service`, `ui/dpg/storage_settings.py`
