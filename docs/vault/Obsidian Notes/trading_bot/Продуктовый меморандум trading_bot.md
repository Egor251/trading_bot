# trading_bot: микроядерная платформа алгоритмической торговли

**Статус:** vision v0.1 (2026-07)  
**Аудитория:** продукт, разработчики  
**Связано:** [[Карта проекта]], [[Архитектура — целевая (microkernel)]], [[DATA FLOW]], [[Universe selection]], [[Философия архитектуры]]

> trading_bot — не «бот с одной стратегией на MACD». Это **конструктор** для подключения брокеров, стратегий и правил исполнения.  
> Философия: **максимальная настраиваемость** и **максимальная адаптивность** — в том числе в отборе бумаг ([[Universe selection]]).  
> Конкретная торговая система — **профиль сборки** (QUIK + mean-reversion + risk rules + universe profile), а не идентичность продукта.

---

## 1. Видение продукта

trading_bot — **микроядерная платформа** для алгоритмической торговли на базе **microkernel_core**.

Ядро не знает заранее, какой брокер, какая стратегия и **какой критерий отбора инструментов** будут подключены. Оно даёт:

- единый **жизненный цикл** сервисов (брокеры, universe) и плагинов (стратегии);
- **шину событий** для рыночных данных и торговых сигналов;
- **настраиваемый triage** инструментов (`universe.*`) — не зашитый top-N;
- **Rule Engine** — связывает сигналы стратегий, риск-чеки и исполнение заявок;
- **REPL** в стиле Metasploit — операторский интерфейс на этапе разработки;
- задел под **UI** (desktop/web) без переписывания доменной логики.

**Парадигма GateGuardian, перенесённая на рынок:**

| GateGuardian | trading_bot |
|--------------|-------------|
| TrafficService → пакеты | MarketDataService → свечи, стакан, портфель |
| triage / fan-in | universe_service → active book (профили) |
| UEBA plugin → анализ | Strategy plugin → сигналы BUY/SELL |
| RuleEngine → реакция | RuleEngine → риск + исполнение |
| nmap TRIGGERED | OrderExecution TRIGGERED |

---

## 2. Философия (столпы)

Канон: [[Философия архитектуры]] — четыре принципа. На продуктном уровне:

| Столп | Смысл |
|-------|--------|
| **Максимальная настраиваемость** | YAML / profiles / rules: стратегии, риск, **и отбор бумаг** (manual, liquid, volatility, futures, options…) |
| **Максимальная адаптивность** | Смена режима, весов консилиума, **и** профиля universe / union books без переписывания ядра |
| **Сервисы чувствуют, плагины думают** | Брокер публикует `market.*` по active. Стратегия не знает QUIK/MT5. |
| **Universe режет поток** | Catalog → настраиваемый screen → active; см. [[Universe selection]] |
| **Правила, а не хардкод** | Сигнал → риск → ордер через Rule Engine |
| **Pull legacy → push target** | Push-события вместо `while True` poll |
| **REPL сейчас, UI потом** | Тонкий клиент к тому же ядру |

---

## 3. Ключевые архитектурные принципы

### 3.1. microkernel_core + host-проект

```
trading_bot (host)
│
├── microkernel_core (зависимость)
│   ├── MicrokernelCore — boot / start / shutdown
│   ├── EventBus + KernelEvent
│   ├── PluginManager + manifest.yaml
│   ├── ServiceRegistry
│   └── RuleEngine (XML)
│
├── services/                    ← бывшие Drivers + triage
│   ├── universe_service/        ← catalog → candidates → active
│   ├── quik_market_service/
│   ├── mock_market_service/     ← follow_universe: только active
│   ├── metatrader_service/      ← задел
│   └── historical_data_service/
│
├── engine/                      ← UniverseManager, Risk, Order, Ensemble
│
├── plugins/                     ← бывшие Strategies + исполнение
│   ├── test_strategy/
│   ├── order_execution/
│   └── analytics/               ← metrics, viz
│
├── host/
│   ├── trading_bot_core.py      ← обёртка MicrokernelCore
│   └── repl/                    ← UI.py → thin client
│
├── config/
│   ├── trading_bot.yaml         ← в т.ч. блок universe:
│   └── universe_catalog.yaml
├── data/rules/                  ← торговые правила
└── legacy/                      ← текущий код (freeze)
    ├── structy.py, UI.py, Drivers/, ...
```

### 3.2. Разделение ответственности

| Компонент | Делает | Не делает |
|-----------|--------|-----------|
| **Universe Service** | Отбор catalog → active, `universe.*` | Индикаторы, ордера |
| **Market Service** | Подключение к брокеру, стрим `market.*` **по active** | MACD, решения о сделках |
| **Strategy Plugin** | Анализ, `strategy.signal` | Прямой вызов `set_transaction` |
| **Rule Engine** | Корреляция сигнал + риск + контекст | Получение свечей |
| **Order Plugin** | Исполнение по action из правила | Логика входа в позицию |
| **REPL** | Команды оператора, introspection | Бизнес-логика торговли |

### 3.3. Legacy vs Target

**Legacy (2022–2024)** — ~1500 строк, работает с QUIK:

- наследование `Strat` → динамический `Driver` через `eval`;
- polling в `test_strategy.main()`;
- SQLite как runtime-конфиг.

**Target (2026+)** — тот же домен, архитектура GateGuardian/microkernel:

- сервисы публикуют, плагины подписываются;
- RuleEngine на событиях;
- явный lifecycle;
- legacy живёт параллельно до parity.

---

## 4. Профили сборки (примеры)

| Профиль | Сервисы | Плагины | Правила |
|---------|---------|---------|---------|
| **Paper SBER** | universe (manual) + mock | test_strategy | signal → log only |
| **Screener paper** | universe (screener) + mock | test_strategy | top-N ликвидности → active |
| **QUIK live** | universe + quik_market | macd_strategy | signal + risk → order_execution |
| **Backtest** | historical_data_service | любая стратегия | replay + metrics |
| **Research** | historical + yahoo | analytics | — |

---

## 5. Интерфейсы

### Сейчас: REPL (Metasploit-style)

```
Command: show strategy
Command: use strategy test_strategy
Strategies/test_strategy: run TQBR SBER
Command: show options
Command: help
Command: exit
```

Сохраняем UX: **модуль → контекст → действие**. Меняем только backend: команды идут в `TradingBotCore`, а не в `eval('self.' + mod)`.

### Потом: UI

- Desktop (PyQt/Electron/Tauri) или Web — **не решено**;
- UI подписывается на те же события / REST поверх ядра;
- REPL остаётся для отладки и автomation.

---

## 6. Что сознательно не в ядре

Как и в microkernel_core README:

- SQLite/UniversalDataStore — хост-проект (для истории сделок, настроек);
- FastAPI — опциональный слой;
- UI — отдельный эпик;
- Multi-tenant / SaaS — не планируется;
- Конкретные alpha-стратегии — только как demo plugins;
- **Live на весь рынок** — сознательно нет; только active book ([[Universe selection]]).

---

## 7. Roadmap (высокий уровень)

1. **Документация + Event Contract + Universe** ← канон universe заложен  
2. **Scaffold host** — microkernel_core, yaml, boot ← **есть**  
3. **universe_service + MockMarketService + test_strategy** — vertical slice ← **есть**  
4. **QuikMarketService** — перенос QUIK, подписки = active  
5. **RuleEngine + OrderExecution** — замена `set_transaction`  
6. **REPL host** — msf-команды поверх ядра  
7. **Backtest / optimizer** + catalog metrics для screener  
8. **UI** — отдельный эпик  

См. [[Universe selection]], [[TODO/Эпик — Universe selection]].

---

## 8. Связь с другими проектами

| Проект | Урок для trading_bot |
|--------|----------------------|
| **GateGuardian** | Service → EventBus → selective plugin; RuleEngine |
| **Aphina** | manifest, PLUGIN_ID, lifecycle (без «шина связывает всё») |
| **microkernel_core** | Эталонный скелет — не копировать GG целиком |
| **Legacy trading_bot** | QuikPy-интеграция, metrics, optimizer — переиспользовать код |
