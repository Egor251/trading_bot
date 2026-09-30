# trading_bot — открытые задачи

**Сводка backlog.** Детальные эпики — в `docs/vault/Obsidian Notes/trading_bot/TODO/`.  
**Канон продукта:** [`docs/architecture/adaptive_ensemble_trading_bot.md`](architecture/adaptive_ensemble_trading_bot.md).

**Обновлено:** 2026-07-28 (этап 6 Strategy SDK закрыт; следующий — Consilium)

---

## План доведения (этапы 0–11)

| Этап | Фокус | Статус |
|------|--------|--------|
| 0 | Secrets hygiene + docs baseline | **сделано** |
| 1 | Trading invariants (Position, PnL, DD, leverage) | **сделано** |
| 2 | Crypto paper / Consilium E2E (+ RuleEngine path) | **сделано** |
| 3 | Budget layout (`compute_flex_width`) | **сделано** |
| 4 | Design system + operator UX polish | **сделано** |
| 5 | Crypto screener, shared picker, pin, per-strategy books | **сделано** |
| 6 | Strategy SDK + 4 crypto-стратегии | **сделано** |
| 7 | Adaptive Consilium → research → live diagnose → classify | **сделано** (A–E) |
| 8+ | Phantom worker → CCXT live → ops | backlog |

---

## Продукт: адаптивный консилиум (новый фокус)

| # | Этап | Задача | Статус |
|---|------|--------|--------|
| E0 | Docs | Техдок v1.0 в `docs/architecture` + vault | **сделано** |
| E1.1 | 1 | Каркас `engine/` (risk, order, ensemble, market, universe, runtime) | **сделано** |
| E1.1b | 1 | Universe selection (manual/screener + service) | **сделано** |
| E1.1c | 1 | Paper path: runtime + candle store + risk/order wire + E2E | **сделано** |
| E1.2 | 1 | Universal MarketDataPort + mock/ccxt/quik transport | **сделано** (+ QUIK broker path, CCXT Consilium paper E2E) |
| E1.2b | 1 | historical_data_service (yahoo/moex → history.candle) | **каркас** |
| E1.2c | 1 | catalog metrics refresh + history cache | **каркас** |
| E1.2d | 1 | MOEX ISS listings → universe catalog | **каркас** |
| E1.3 | 1 | Базовая трендовая стратегия-плагин | **сделано** (`ma_cross_example` + `StrategyPlugin`) |
| E1.4 | 1 | OrderManager + RiskManager (DD / size / leverage) | **сделано** (signed netting, realized/unrealized PnL, reduce-only under halt) |
| E1.5 | 1 | Хранилище (`cb_storage` + journal / `show storage`) | **сделано** (каркас + REPL) |
| E1.6 | 1 | AI Analyst plugin (`Code_base/ai_analyst` + trading tools) | **каркас** |
| E1.7 | 1 | Math engine (`Code_base/cb_math` + `engine/indicators`) | **каркас** |
| E2.0 | 2 | History replay → `market.candle` + `backtest.run_*` | **каркас** |
| E2.1 | 2 | Phantom shadow ledger + multi-variant grid (replay) | **каркас** |
| E2.* | 2 | Optuna/плато, Walk-Forward | **C каркас** (`engine/research.py` + `phantom.run_walk_forward`) |
| E3.0 | 3 | Consilium wiring (`ensemble_service` → `ensemble.decision`) | **каркас** |
| E3.0b | 3 | Consilium → RuleEngine → order_execution + mean-reversion | **каркас** |
| E3.* | 3 | Bandit, Risk Parity, corr matrix, more strategies | **A+B сделано** (rebalance + soft regime) |
| E4.* | 4 | Regime / Meta-Labeling / NLP | backlog |
| E5.0 | 5 | Operator Tauri + static Next + tray/notifications | **каркас** |
| E5.0b | 5 | Browser web Operator (`--web`, remote/server) | **каркас** |
| E5.0c | 5 | Depth parity vs legacy DPG (Desk/Phantom/Strategy/i18n) | **backlog** |
| E5.1 | 5 | App-shell (menu/sidebar/pages) + bridge control plane | **сделано** (graceful TCP lifecycle) |
| E5.2 | 5 | Operator IA Desk/Book/Consilium/AI + strategy votes | **каркас** |
| E5.3 | 5 | Visual foundation + deterministic budget layout | **сделано** (2026-07 pass) |
| E5.* | 5 | OHLC polish, phantom curves, ZMQ polish | backlog |
| E6.* | 6 | Фантом 2–3 мес. → mini live | backlog |

---

## Критично (переход на microkernel_core)

| # | Задача | Статус |
|---|--------|--------|
| 1 | Подключить `microkernel_core` | **сделано** |
| 2 | Event Contract (`universe.*`, `market.*`, `strategy.*`, `execution.*`, `system.halt`) | **v0.1** |
| 2b | Universe selection канон + service | **сделано** |
| 2c | TradingRuntime + paper E2E + kill switch | **сделано** |
| 3 | `TradingBotCore` boot/start/shutdown | **сделано** |
| 4 | `config/trading_bot.yaml` + services/plugins | **сделано** |
| 5 | Legacy freeze | **утверждено** |

---

## Высокий приоритет (инфра v2)

| # | Задача | Статус |
|---|--------|--------|
| 6 | `QuikMarketService` | **сделано** (fake E2E + broker wire; live terminal TBD) |
| 7 | `MockMarketService` | **сделано** (через MarketAdapterServiceBase) |
| 7b | `CcxtMarketService` | **сделано** (Binance public feed + deterministic paper E2E) |
| 8 | `test_strategy` plugin | **сделано** |
| 9 | `OrderExecutionPlugin` paper | **сделано** (через TradingRuntime) |
| 10 | REPL | **сделано** (storage/universe/history, halt/resume) |
| 11 | XML rules signal → execution | **сделано** (paper_buy_on_signal) |
| 12 | `trading_runtime_service` + candle store + halt | **сделано** |
| 13 | `historical_data_service` | **каркас** (yahoo/moex, cache, catalog metrics) |
| 14 | AI Analyst plugin | **каркас** (mock + trading tools + `ask`) |
| 15 | `cb_math` + indicators | **каркас** |
| 16 | `ensemble_service` | **каркас** (equal weights → `ensemble.decision`) |
| 17 | `phantom_service` | **каркас+** (shadow grid/patterns, timeline, PositionSizer, fill alignment) |
| 17b | `position_sizing` | **сделано** (`engine/position_sizing.py` → phantom + `submit_signal`; см. `docs/architecture/position_sizing.md`) |
| 18 | Operator UI | **каркас+** (Phantom 3-zone + risk/sizing knobs + strategy schema editor) |

---

## Не в scope сейчас

- Live без paper/mock
- Multi-account orchestration
- Legacy удалён из `main` (тег `legacy-v1`)
- HFT

---

## Code_base

| Ресурс | Путь |
|--------|------|
| microkernel_core | `Code_base/microkernel_core/` |
| plugin_manager | `Code_base/plugin_manager/` |
| cb_rule_engine | `Code_base/cb_rule_engine/` (optional XML rules) |
| cb_storage | `Code_base/cb_storage/` |
| ai_analyst | `Code_base/ai_analyst/` |
| cb_math | `Code_base/cb_math/` |
| cb_event_bus | `Code_base/rust/crates/cb_event_bus/` |

---

## Ссылки

- Техдок: `docs/architecture/adaptive_ensemble_trading_bot.md`
- Universe: `docs/architecture/universe_selection.md`
- Market adapters: `docs/architecture/market_adapters.md`
- AI Analyst: `docs/architecture/ai_analyst.md`
- Math engine: `docs/architecture/math_engine.md`
- Operator UI: `docs/architecture/operator_ui.md`
- Vault: `docs/vault/Obsidian Notes/trading_bot/`
- Launch: `docs/runbooks/launch.md`
- Меморандум / DATA FLOW / Event Contract / Universe selection — в vault
