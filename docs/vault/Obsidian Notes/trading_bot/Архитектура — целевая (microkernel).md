# Архитектура — целевая (microkernel)

**Статус:** целевая спека (2026-07)  
**Аудитория:** разработчики, AI-агенты  
**Связано:** [[Продуктовый меморандум trading_bot]], [[Event Contract]], [[DATA FLOW]], [[Universe selection]], [[Философия архитектуры]]

> Описывает **куда идём**. Текущий код — [[Архитектура — legacy (текущий код)]].

---

## Продукт в одном абзаце

**trading_bot** (target) — asyncio-хост на **microkernel_core**: **universe** (чем торговать) + market services + strategy plugins + EventBus + XML Rule Engine + REPL. Точка входа: `host/main.py` → `TradingBotCore.boot()` → REPL или headless.

Live-данные только по **active book**; полный каталог не стримится (см. [[Universe selection]]).

---

## Целевая структура репозитория

```
trading_bot/
├── host/
│   ├── main.py                      # bootstrap
│   ├── trading_bot_core.py          # MicrokernelCore + domain hooks
│   └── repl/
│       ├── __init__.py
│       └── shell.py                 # бывший UI.py (msf-style)
├── config/
│   ├── trading_bot.yaml             # в т.ч. universe:
│   └── universe_catalog.yaml        # seed / listings для screener
├── services/
│   ├── universe_service/            # catalog → candidates → active
│   ├── quik_market_service/
│   │   ├── __init__.py              # QuikMarketService
│   │   └── quik_connector.py        # QuikPy wrapper (из Drivers/Quik.py)
│   ├── mock_market_service/         # follow_universe → только active
│   └── historical_data_service/
├── engine/                          # UniverseManager, Risk, Order, Ensemble
├── plugins/
│   ├── test_strategy/
│   │   ├── manifest.yaml
│   │   └── plugin.py
│   ├── order_execution/
│   └── macd_strategy/               # будущее
├── data/
│   └── rules/                       # XML торговые правила
├── docs/todo.md
├── Документация/
├── legacy/                          # freeze: текущий корень
│   ├── UI.py, structy.py, Drivers/, Strategies/, ...
│   └── README.md                    # «не расширять»
└── requirements.txt                 # + microkernel_core (path или pip)
```

---

## Зависимость microkernel_core

```python
from microkernel_core import MicrokernelCore, CoreSettings

class TradingBotCore:
    def __init__(self):
        self.kernel = MicrokernelCore(
            CoreSettings(
                config_path="config/trading_bot.yaml",
                services_dir=Path("services"),
            )
        )

    async def boot(self):
        await self.kernel.boot()
        # опционально: SQLite store, REPL bindings

    async def start(self):
        await self.kernel.start()
```

**Не дублировать** в host: EventBus, PluginManager, ServiceRegistry, RuleEngine — только расширять.

---

## config/trading_bot.yaml (черновик)

```yaml
core:
  name: trading_bot
  version: "2.0.0"
  log_level: INFO

instance:
  id: local
  role: standalone

plugins:
  paths:
    - plugins
  exclude: []

services:
  exclude:
    - base_service
  # явный список или автodiscovery из services/

  quik_market_service:
    enabled: false
    account: ${QUIK_ACCOUNT}
    class_codes: [TQBR, SPBFUT]

  mock_market_service:
    enabled: true
    follow_universe: true

universe:
  mode: manual                 # manual | screener
  static_active: [TQBR:SBER, TQBR:GAZP]
  include: []
  exclude: []
  limits:
    max_candidates: 50
    max_active: 10
    min_price: 1.0
    min_dollar_volume: 1000000

market:
  default_class_code: TQBR
  # default_tickers — legacy; предпочтительно universe.static_active

rules:
  enabled: true
  path: data/rules

event_bus:
  max_queue_size: 50000

repl:
  enabled: true
  prompt_style: metasploit   # Command: | module/path:
```

Канон universe: [[Universe selection]], `docs/architecture/universe_selection.md`.

---

## ServiceRegistry — market services + universe

### universe_service

- Читает catalog (`config/universe_catalog.yaml` или broker listings)
- `UniverseManager` (`engine/universe.py`): manual | screener
- Публикует `universe.snapshot`, `universe.changed`
- Market services подписываются и стримят **только active**

Базовый контракт market service (`microkernel_core.services.BaseService` + domain mixin):

| Метод / поведение | Описание |
|-------------------|----------|
| `start()` | Подключение к брокеру, фоновые задачи |
| `stop()` | Disconnect, cleanup |
| subscribe `universe.changed` | Обновить набор стримимых тикеров |
| publish `market.candle` | OHLCV + metadata (**active only**) |
| publish `market.dom` | Стакан |
| publish `market.portfolio` | Позиции, заявки, cash |
| publish `market.order_update` | Изменение заявки/сделки |

### quik_market_service

Источник: `legacy/Drivers/Quik.py` (~416 строк).

- QuikPy singleton, callbacks OnOrder/OnTrade/…
- Нормализация в `KernelEvent` payload (см. [[Event Contract]])
- **Не** содержит торговой логики
- Подписки на тикеры = active book (не весь TQBR)

### mock_market_service

Источник: `legacy/Drivers/TestDriver.py`.

- Deterministic/fake candles для dev и unit-тестов
- `follow_universe: true` — тикеры из `universe.changed`
- Default enabled в dev-конфиге

### historical_data_service

Источник: `legacy/get_finance_data.py`, `Info/alphavantage.py`.

- On-demand или batch: `market.history.candle`
- Не путать со live stream; может кормить catalog metrics для screener

---

## PluginManager — strategies & actions

### Strategy plugin (CONTINUOUS или EVENT-DRIVEN)

```python
from microkernel_core import BasePlugin, PluginRunMode

class TestStrategyPlugin(BasePlugin):
    PLUGIN_ID = "test_strategy"
    RUN_MODE = PluginRunMode.CONTINUOUS

    async def on_initialize(self):
        await self.subscribe_event("market.candle", self._on_candle)

    async def _on_candle(self, event: KernelEvent):
        ticker = event.payload["ticker"]
        # ... internal state ...
        if self._should_signal(event.payload):
            await self.publish_event("strategy.signal", {
                "action": "BUY",
                "ticker": ticker,
                "quantity": 10,
                "reason": "test",
            })
```

**manifest.yaml:**

```yaml
id: test_strategy
name: Test Strategy
version: "1.0.0"
run_mode: continuous
entrypoint: plugin:TestStrategyPlugin
dependencies: []
config:
  tickers: [SBER]
  parameters:
    a: { default: 100, min: 0, max: 1000 }
```

### order_execution plugin (TRIGGERED)

```python
class OrderExecutionPlugin(BasePlugin):
    PLUGIN_ID = "order_execution"
    RUN_MODE = PluginRunMode.TRIGGERED

    async def execute_action(self, action_name, params, context):
        if action_name == "place_order":
            # делегировать quik_market_service через core или direct API
            ...
```

---

## RuleEngine — торговые правила

Пример XML (концепт):

```xml
<rule id="test_buy_with_risk" enabled="true">
  <conditions>
    <event source="test_strategy" type="strategy.signal"/>
    <check plugin="order_execution" name="within_risk_limits"/>
  </conditions>
  <actions>
    <action plugin="order_execution" name="place_order"/>
  </actions>
</rule>
```

RuleEngine подписан на `*` (как в microkernel_core) — матчит `strategy.signal`, вызывает checks, триггерит actions.

---

## REPL host

См. [[REPL — CLI интерфейс]]. Backend:

| Legacy команда | Target API |
|----------------|------------|
| `show strategy` | `plugin_manager.list_plugins(kind=strategy)` |
| `use strategy X` | `repl_context.set_active_plugin("X")` |
| `show options` | manifest config schema + runtime values |
| `run ...` | `plugin.execute` / publish command event |
| `optimize` | invoke BacktestPlugin или CLI wrapper |

---

## Хранилище (хост-слой)

microkernel_core **без БД**. В trading_bot:

| Данные | Хранение |
|--------|----------|
| Runtime settings | ConfigManager + optional SQLite (`legacy/database.py` → refactor) |
| История сделок | SQLite / CSV (host) |
| OHLCV cache | Parquet / SQLite (optional) |
| Optimizer runs | SQLite table `optimiser` (уже был задел) |

---

## Lifecycle

```
main.py
  → TradingBotCore()
  → await kernel.boot()
       → config load
       → event_bus.start()
       → services discover + initialize
       → plugins load (manifest) + initialize
       → rule_engine.initialize + subscribe *
  → await kernel.start()
       → mock/quik service start → publish market.*
       → strategy plugins subscribe
  → REPL.run()  OR  await idle
  → await kernel.shutdown()
```

**Без** `sleep(2)` в `__init__`, **без** `eval` dispatch.

---

## Parity checklist (legacy → target)

| Legacy | Target | Критерий готовности |
|--------|--------|---------------------|
| TestDriver candles | mock_market_service | REPL `run` видит свечи в логе |
| Quik get_candles | quik_market_service | `market.candle` в шине |
| Quik set_transaction | order_execution + rule | заявка через правило |
| test_strategy loop | test_strategy plugin | сигнал на mock |
| UI show/use | repl/shell.py | те же команды |
| optimizer | backtest plugin | перебор params |

---

## Ссылки

- [[Universe selection]] — чем торговать; active book
- [[Философия архитектуры]]
- [[Event Contract]] · [[DATA FLOW]]
- microkernel_core: `Code_base/microkernel_core/README.md`
- GateGuardian reference: `easm-gate-guardian/Документация/`
- Эпики: [[TODO/README]], [[TODO/Эпик — Universe selection]]
