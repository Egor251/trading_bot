# Эпик — Strategies → Plugins

**Приоритет:** P1  
**Статус:** сделано (каркас)  
**Связано:** [[Event Contract]], [[TODO/Эпик — Drivers → Services]]

---

## Цель

Стратегии — **отдельная сущность** (торговая логика).  
Технически грузятся тем же `plugin_manager`, что и расширения (AI и т.п.) — как темы UI и плагины в GG: один менеджер, разные вещи.

`Strategies/*.py` → пакет в `plugins/` или `plugins_local/` + `StrategyPlugin` + `manifest.yaml`.

---

## Что есть

- [x] `host/bootstrap.py` — явный PYTHONPATH на `plugin_manager` / microkernel / cb_storage
- [x] Discovery: `plugins.paths` + `plugins.installed_paths` (`plugins_local/` для приватных)
- [x] `plugins/test_strategy`, `plugins/ma_cross_example`, `plugins/order_execution`
- [x] `engine.strategy_plugin.StrategyPlugin`
- [x] REPL: `show strategy`, `show plugins`, `show options`
- [x] `.gitignore` — приватные стратегии не в публичный реп

---

## Куда класть новую стратегию

1. `plugins_local/my_edge/manifest.yaml` + `plugin.py` (наследование `StrategyPlugin`)
2. Опционально конфиг в `trading_bot.yaml` под `plugins.my_edge:`
3. Рестарт хоста → `show plugins` / `use strategy my_edge`

---

## Follow-up

- [ ] Hot-reload одного плагина без полного restart
- [ ] analytics plugin
- [ ] config schema / optimize ranges в manifest
