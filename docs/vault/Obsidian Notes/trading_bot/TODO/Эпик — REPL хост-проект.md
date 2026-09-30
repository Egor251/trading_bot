# Эпик — REPL хост-проект

**Приоритет:** P1  
**Статус:** черновик  
**Связано:** [[REPL — CLI интерфейс]], [[TODO/Эпик — интеграция microkernel_core]]

---

## Цель

Перенести `UI.py` в `host/repl/shell.py` с **тем же UX**, backend на microkernel — **без eval**.

---

## Задачи

- [ ] `host/repl/shell.py` — TradingRepl class
- [ ] Command parser: split, validate, dispatch dict
- [ ] Prompt context: `Command` → `{plugin_id}` 
- [ ] `show strategy|driver|info|options`
- [ ] `use strategy <id>`
- [ ] `run [args]` — delegate to active plugin
- [ ] `help`, `exit` — shutdown kernel
- [ ] `optimize` — stub → BacktestPlugin epic
- [ ] `set` — runtime config update
- [ ] Fake "System check" / "Database connected" → kernel health (optional nostalgia)

---

## Parity matrix

| Legacy UI.py | Target repl | Must match UX |
|--------------|-------------|---------------|
| show strategy table | plugin list + manifest description | ✅ |
| use strategy X | active plugin | ✅ |
| show options | config parameters | ✅ |
| eval dispatch | method dispatch | behavior only |

---

## Entry points

```
# legacy (until moved)
python UI.py

# target
python host/main.py --repl
python -m host.repl
```

---

## Критерии готовности

Оператор может пройти сценарий:
```
show strategy → use strategy test_strategy → show options → run TQBR SBER → exit
```
на mock stack без legacy UI.py.
