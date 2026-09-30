# TODO — эпики и крупные изменения

**Назначение:** архитектурная миграция legacy → microkernel_core. **Не текущий спринт** — platform work.

**Сводная таблица:** `docs/todo.md` (корень code-репо).

---

## Оглавление эпиков

| Эпик | Приоритет | Суть |
|------|-----------|------|
| [[Эпик — интеграция microkernel_core]] | P0 | Scaffold host, yaml, boot lifecycle |
| [[Эпик — Event Contract]] | P0 | Спека и реализация market/strategy/execution/universe events |
| [[Эпик — Universe selection]] | P0 | Catalog → active; не стримить весь рынок |
| [[Эпик — legacy freeze]] | P0 | Не расширять legacy; только перенос |
| [[Эпик — Drivers → Services]] | P1 | Quik, Mock, MT5, Transaq → services/ |
| [[Эпик — Strategies → Plugins]] | P1 | Стратегии как plugins + manifest |
| [[Эпик — Rule Engine и исполнение]] | P1 | Правила, order_execution, risk checks |
| [[Эпик — REPL хост-проект]] | P1 | UI.py → repl/shell.py без eval |
| [[Эпик — источники данных]] | P2 | Historical Yahoo/MOEX/AlphaVantage + catalog metrics |
| [[Эпик — бэктест и оптимизация]] | P2 | optimizer → BacktestPlugin |
| [[Эпик — хранилище]] | P2 | SQLite, сделки, optimizer runs |
| [[Эпик — UI]] | P3 | Desktop/Web — будущее |
| [[Эпик — адаптивный консилиум]] | P1 | Ensemble / phantom / meta (техдок) |

---

## Порядок выполнения (рекомендуемый)

```
1. интеграция microkernel_core + Event Contract (черновик)
2. universe_service + mock_market_service + test_strategy  ← vertical slice (+ active book)
3. REPL host (parity команд show/use/run)
4. quik_market_service (подписки = active)
5. Rule Engine + order_execution
6. historical + catalog metrics + screener mode
7. legacy/ move + cleanup
8. UI (отдельно)
```

---

## Соглашения

1. **Эпик** — multi-file; заголовок `Эпик — …`.
2. Статусы: `черновик` | `обсуждение` | `утверждено` | `в работе` | `сделано`.
3. Legacy **работает** — не ломать до checkbox parity в эпике интеграции.
4. Мелкий техдолг — `docs/todo.md` § «Низкий приоритет».

---

## Связанные документы

- [[Карта проекта]]
- [[Продуктовый меморандум trading_bot]]
- [[Архитектура — целевая (microkernel)]]
- microkernel_core: `Code_base/microkernel_core/`
