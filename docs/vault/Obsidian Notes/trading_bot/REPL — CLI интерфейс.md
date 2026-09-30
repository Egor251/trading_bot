# REPL — CLI интерфейс

**Статус:** spec (2026-07)  
**Связано:** [[Архитектура — legacy (текущий код)]], [[Архитектура — целевая (microkernel)]], [[TODO/Эпик — REPL хост-проект]]

> Metasploit-style консоль **сохраняем**. Меняется только backend: вместо `eval` — API `TradingBotCore` / `microkernel_core`.

---

## UX (не менять)

```
System check
Database connected
Command: show strategy
Command: use strategy test_strategy
Strategies/test_strategy: run TQBR SBER
Strategies/test_strategy: show options
Strategies/test_strategy: help
Command: exit
```

### Паттерн msf

1. **Контекст** — prompt показывает активный модуль (`Strategies/test_strategy` → `test_strategy`)
2. **Introspection** — `show <type>` списки модулей
3. **Select** — `use <type> <name>`
4. **Action** — `run`, `optimize`, `set` (в контексте модуля)
5. **Global** — `help`, `exit`

---

## Команды (legacy → target)

| Команда | Legacy | Target |
|---------|--------|--------|
| `show strategy` | parse_dir Strategies/ | PluginManager list `kind=strategy` |
| `show driver` | parse_dir Drivers/ | ServiceRegistry list market services |
| `show info` | parse_dir Info/ | historical_data / info plugins |
| `show options` | parse_file optimization_parameters | manifest `config.parameters` |
| `use strategy X` | set current_state path | `repl_context.active = "X"` |
| `use driver X` | N/A (driver from DB) | enable/switch default service in config |
| `run [args]` | Strategy.run via eval | invoke plugin / publish run event |
| `optimize` | Optimizer.optimize | BacktestPlugin CLI |
| `set [param val]` | stub | update runtime config → ConfigManager |
| `help` | static table | static + dynamic from manifest |
| `exit` | break loop | kernel.shutdown() |

---

## Legacy implementation (reference)

Файл: `UI.py`

```python
actions = ['use', 'run', 'set', 'help', 'exit', 'show', 'optimize']
module_types = {'strategy': 'Strategies', 'driver': 'Drivers', 'info': 'Info', 'options': 'options'}

# dispatch:
eval('self.' + mod + '(command[1:])')
```

**Target:** dict dispatch или match/case — **никакого eval**.

---

## Target: host/repl/shell.py

```python
class TradingRepl:
    def __init__(self, core: TradingBotCore):
        self.core = core
        self.prompt = "Command"
        self.active_plugin_id: str | None = None

    async def run(self):
        while True:
            line = input(f"{self.prompt}: ")
            # parse → dispatch_command(parts)
```

### Backend adapters

| Repl method | Kernel call |
|-------------|-------------|
| `_show_strategies()` | `core.kernel.plugin_manager.plugins` filtered |
| `_show_services()` | `core.kernel.service_registry.services` |
| `_use_strategy(name)` | validate plugin exists, set prompt |
| `_run(args)` | get plugin, call `run_once()` or strategy-specific CLI |

---

## Driver → Service в REPL

Legacy термин **driver** в UX **можно оставить** для оператора:

```
Command: show driver
Command: use driver mock
```

Backend: переключение `services.mock_market_service.enabled` или hot-swap active broker service (эпик — TBD).

---

## Будущий UI

| REPL | UI |
|------|-----|
| `show strategy` | sidebar plugin list |
| `use strategy X` | select active strategy panel |
| `show options` | settings form from manifest schema |
| `run` | Start/Stop button |
| stream logs | event bus tap → WebSocket |

REPL и UI — **два клиента одного ядра**, не два приложения.

---

## Эпик

Детали реализации: [[TODO/Эпик — REPL хост-проект]].
