# Эпик — интеграция microkernel_core

**Приоритет:** P0  
**Статус:** в работе → **vertical slice готов** (mock + strategy + paper rule)  
**Связано:** [[Архитектура — целевая (microkernel)]], [[TODO/Эпик — legacy freeze]]

---

## Цель

Подключить `Code_base/microkernel_core` как зависимость и поднять **TradingBotCore** с корректным lifecycle, не ломая legacy.

---

## Задачи

- [ ] `requirements.txt`: `-e ../Code_base/microkernel_core` или git submodule
- [x] `config/trading_bot.yaml` из example
- [x] `host/trading_bot_core.py` — обёртка `MicrokernelCore`
- [x] `host/main.py` — `asyncio.run(main())`, boot/start/shutdown
- [x] Каталоги `services/`, `plugins/`, `data/rules/`
- [x] `legacy/README.md` — «freeze, не расширять»
- [x] Hello vertical: mock service + test_strategy + order_execution
- [x] CI smoke: `python host/main.py --smoke` 

---

## Критерии готовности

1. `await core.boot(); await core.start()` без ошибок с mock service
2. EventBus получает хотя бы одно `market.candle` от mock
3. Legacy `python UI.py` по-прежнему запускается из корня (до move)

---

## Не в scope

- Quik live
- REPL parity
- Удаление structy.py
