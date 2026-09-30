# trading_bot

Алгоритмический торговый бот: микроядро + плагины + сервисы + отдельный Operator UI.

**Философия:** микроядерность · модульность · настраиваемость · адаптивность  
(стратегии / консилиум / universe). Каркас — из **Code_base**; домен — в этом репо.

| Документ | Путь |
|----------|------|
| Философия | [`docs/architecture/philosophy.md`](docs/architecture/philosophy.md) |
| Universe | [`docs/architecture/universe_selection.md`](docs/architecture/universe_selection.md) |
| Консилиум | [`docs/architecture/adaptive_ensemble_trading_bot.md`](docs/architecture/adaptive_ensemble_trading_bot.md) |
| Math engine | [`docs/architecture/math_engine.md`](docs/architecture/math_engine.md) |
| Operator UI | [`docs/architecture/operator_ui.md`](docs/architecture/operator_ui.md) |
| Launch | [`docs/runbooks/launch.md`](docs/runbooks/launch.md) |
| Backlog | [`docs/todo.md`](docs/todo.md) |
| Vault (Obsidian) | [`docs/vault/`](docs/vault/) |

## Быстрый старт

```bash
cd trading_bot
pip install -r requirements.txt
# or: pip install -e ".[dev]"

python main.py --smoke
python main.py --repl
python -m pytest -q
```

Секреты не в YAML: скопируйте `.env.example` или задайте
`CURSOR_API_KEY`, `CCXT_API_KEY` / `CCXT_SECRET`, `ALPACA_API_KEY` /
`ALPACA_SECRET_KEY` в окружении.

Operator UI (Tauri desktop primary; Next.js web secondary):

```bash
python run_operator.py              # Tauri + BFF + engine
python run_operator.py --web        # BFF :8787 + Next :3000
# или по частям:
python host/main.py --headless
python -m ui --web
cd ui/desktop && npm install && npm run dev
```

Entry points after `pip install -e .`: `algotrader`, `algotrader-operator`.

## Структура

```text
trading_bot/            # корень репозитория
  engine/               # risk, order, universe, runtime, indicators
  host/                 # TradingBotCore, REPL, Engine entry
  services/             # infra: runtime, universe, storage, bridge, ensemble…
  strategies/           # StrategyPlugin (trading strategies)
  strategies_local/     # private strategies (gitignored)
  plugins/              # non-strategy plugins (AI, execution, market/)
  plugins_local/        # private extension plugins (gitignored)
  ui/                   # Operator: web (Next) + desktop (Tauri) + BFF
    web/                # Next.js Operator (static export for Tauri)
    desktop/            # Tauri 2 shell
  config/               # trading_bot.yaml, universe_catalog.yaml
  scripts/              # CLI wrappers (healthcheck)
  tools/                # developer utilities
  ops/                  # prometheus alerts, …
  docs/                 # architecture, runbooks, vault
  tests/
  main.py               # → host.main
  run_operator.py       # engine + UI
  pyproject.toml
```

Market connectors (`ccxt` / `alpaca` / `quik` / `mock` / `historical`) live under
`plugins/market/` and are still registered in the service registry
(config keys `services.*_market_service`).

**Universe:** live только по active book (`manual` whitelist или `screener`).  
См. [universe_selection.md](docs/architecture/universe_selection.md).

Legacy v1 удалён из `main`; точка восстановления: тег `legacy-v1`
(`git checkout legacy-v1`).

## Зависимости Code_base

| Пакет | Роль |
|-------|------|
| `microkernel_core` | EventBus, services, boot |
| `plugin_manager` | загрузчик пакетов |
| `cb_rule_engine` | XML RuleEngine (optional) |
| `cb_math` | SymPy CAS |
| `ai_analyst` | AI Analyst extension |
| `cb_storage` | durable event journal |

Путь: `../Code_base/…` (см. `host/bootstrap.py`).
