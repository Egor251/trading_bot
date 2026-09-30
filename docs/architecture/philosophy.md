# Философия архитектуры trading_bot

**Статус:** канон  
**Эталон продукта:** `easm-gate-guardian` (Hekata)  
**Переносимый каркас:** `Code_base` (`microkernel_core`, `plugin_manager`, опц. `cb_event_bus`, `ai_analyst`)

---

## Четыре принципа продукта

1. **Микроядерная архитектура**  
   Тонкое ядро (boot / EventBus / PluginManager / ServiceRegistry / RuleEngine).  
   Вся доменная логика — снаружи ядра: сервисы и плагины.

2. **Максимальная модульность**  
   Стратегии, брокеры, риск, фантом, мета, UI, **отбор инструментов** — отдельные модули с явными контрактами  
   (`PLUGIN_ID`, manifest, Event Contract, screen profiles). Можно заменить/отключить без переписывания ядра.

3. **Максимальная настраиваемость**  
   Поведение задаётся YAML / settings плагинов / правилами / профилями, а не хардкодом.  
   Включение/исключение через `plugins.exclude`, `services.*.enabled`, `universe.profile`, schemas.  
   Оператор или профиль сборки выбирает *как* система отбирает бумаги, *чем* торгует, *какие* стратегии в консилиуме.

4. **Максимальная адаптивность**  
   Система должна уметь менять поведение под режим рынка, тип стратегии и экономику инструмента  
   (акции / фьючерсы / крипто / опционы) **без переписывания ядра**.  
   Адаптивность консилиума (веса, параметры, regime) и адаптивность **universe** (смена профиля отбора, union books, nomination) — одна философия, разные слои.

> «Максимальная настраиваемость + максимальная адаптивность» — не лозунг для UI.  
> Это относится и к стратегиям, и к риску, и к **истории с отбором бумаг под торговлю**.

---

## Философия на примере Universe

| Принцип | Как проявляется в отборе |
|---------|--------------------------|
| Настраиваемость | `manual` / `screener`, `profile`, `rank_by`, floors, `asset_classes`, include/exclude — всё в YAML |
| Адаптивность | другой профиль под vol vs trend; futures вместо equities; union books в консилиуме; позже — смена профиля по regime / meta |
| Модульность | `UniverseManager` + `universe_service`; screener не зашит в стратегию и не в market adapter |
| Микроядро | ядро не знает «торгуем SBER»; знает события `universe.*` / `market.*` |

**Анти-паттерны (нарушают философию):**

- один жёсткий «top-N по объёму» на весь продукт;
- тикеры, захардкоженные в коде стратегии;
- live на весь каталог «на всякий случай»;
- опционы, притворённые equity-скринером без отдельного chain-path.

Канон деталей: [universe_selection.md](universe_selection.md).

---

## Сущности (продуктовый словарь)

Один **механизм загрузки** (`Code_base/plugin_manager`: manifest + lifecycle) — разные **сущности** в речи и документации.  
Аналогия: в GG темы UI и AI-плагины тоже тянутся менеджером пакетов, но это не одно и то же.

| Сущность | Смысл | Где живёт | Примеры |
|----------|--------|-----------|---------|
| **Service** | инфра ядра (не биржа) | `services/` | universe, runtime, storage, bridge, ensemble, phantom |
| **Market connector** | источник рынка / биржа | `plugins/market/` | ccxt, alpaca, quik, mock, historical |
| **Стратегия** | торговая логика → `strategy.signal` | `strategies/`, `strategies_local/` | `test_strategy`, `ma_cross_example` |
| **Исполнение** | actuator заявок | `plugins/order_execution` | paper/live bridge |
| **Плагин (расширение)** | опциональный аддон платформы | `plugins/`, `plugins_local/` | `ai_analyst` |
| **Engine** | доменные примитивы ядра (не пакет) | `engine/` | Risk, Order, Universe, CandleStore |

В коде стратегия наследует `StrategyPlugin` (удобный SDK поверх менеджера) — это **доставка**, не смешение понятий.  
В UX/доках: «стратегия» и «плагин» — разные слова; REPL: `show strategy` vs `show plugins`.

---

| GateGuardian / Hekata | trading_bot |
|-----------------------|-------------|
| `MicrokernelCore` / host | `TradingBotCore` (`host/`) |
| sensors / market ingest | Market Data services (mock, CCXT, QUIK…) |
| sensor fan-in / triage | `universe_service` — **настраиваемый и адаптивный** triage |
| technical / product plugins | **стратегии** + execution; отдельно — **плагины-расширения** (AI, analytics) |
| RuleEngine + checks | signal → risk → order rules (**`cb_rule_engine`**, optional) |
| EventBus (`market.*` / `security.*`) | Event Contract `universe.*` / `market.*` / `strategy.*` / `execution.*` |
| AI Analyst plugin | meta / regime / (опц. `ai_analyst` из Code_base) |
| UI отдельно от power plane | Tauri / Next + BFF — **не** в процессе Engine |
| config YAML + plugin settings | `config/trading_bot.yaml` + per-plugin + `universe.*` |
| phantom / dual-path validation | Shadow Trading worker |

**Вывод:** trading_bot — тот же класс системы (микроядро + плагины + шина + конфиг),  
но домен = алготрейд / консилиум, а не NDR. Настраиваемость и адаптивность — сквозные, включая universe.

---

## Что уже в Code_base (не копировать заново)

| Пакет | Использование в trading_bot |
|-------|----------------------------|
| `microkernel_core` | ядро Engine |
| `plugin_manager` | единый загрузчик пакетов (стратегии, execution, расширения) |
| `cb_rule_engine` | XML RuleEngine (optional add-on) |
| `cb_event_bus` / `cb_backpressure` | опц. hot-path шина (Rust) |
| `cb_storage` | durable events: SQLite / DuckDB / JSONL / Parquet / ClickHouse / chDB / Postgres / Cockroach + hot buffer + hot reroute |
| — | **Market adapters:** биржа подключается адаптером; драйвер приводит данные к Event Contract (`docs/architecture/market_adapters.md`) |
| `ai_analyst` | **каркас** — extension plugin + REPL `ask` (mock); meta/regime позже |
| `cb_math` | SymPy CAS |

Домен trading_bot (`engine/`, стратегии, phantom, брокеры, universe) живёт **в этом репозитории**.

---

## Правила проектирования

- UI / dashboard **никогда** не в том же процессе, что исполнение (как UI vs power plane в GG).
- Новая фича = plugin или service + события + config, не «ещё один god-module».
- Стратегии ортогональны и отключаемы; консилиум — координатор, не монолит.
- Режим **observe**: стратегия running и пишет `strategy.signal` (`observe_only`), но не голосует в lineup и не исполняется — для аудита «что было бы».
- **Universe:** настраиваемые профили + адаптивная смена/union; критерий отбора не зашит (см. [universe_selection.md](universe_selection.md)).
- Фантом и мета — отдельные процессы/плагины; падение не роняет Engine.
- Legacy v1 удалён из `main` (тег `legacy-v1`); новый код только на microkernel-стеке.

---

## Карта каталогов (целевая)

```
host/          # тонкий хост ядра (как GG core host)
engine/        # risk / order / ensemble / universe — не UI
services/      # universe, runtime, storage, bridge, ensemble, phantom
strategies/    # StrategyPlugin packages
strategies_local/
plugins/       # extensions + order_execution + market/
plugins_local/
phantom/       # shadow worker (отдельный процесс позже)
meta/          # regime / bandit / meta-label (plugin или lib)
ui/            # Operator (Tauri desktop + Next web + FastAPI BFF)
config/        # trading_bot.yaml + universe_catalog.yaml
```

---

## Связанные документы

- [Техдок — адаптивный консилиум](adaptive_ensemble_trading_bot.md)
- [Universe selection — чем торговать](universe_selection.md)
- [Хранение данных — файлы vs БД, hot reroute](storage.md)
- Vault: `Universe selection`, `Целевая архитектура (microkernel)`, `Event Contract`, `DATA FLOW`
- Code_base: `microkernel_core/README.md`, `plugin_manager/README.md`, `rust/README.md`
