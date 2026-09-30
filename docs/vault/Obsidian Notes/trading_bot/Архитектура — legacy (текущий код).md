# Архитектура — legacy (текущий код)

**Статус:** snapshot legacy (2026-07)  
**Аудитория:** разработчики, AI-агенты  
**Связано:** [[Архитектура — целевая (microkernel)]], [[REPL — CLI интерфейс]]

> **Этот код работает.** QUIK, mock, metrics, optimizer — всё на месте. Не расширять; только bugfix и перенос фрагментов в target. Freeze до parity — см. [[TODO/Эпик — legacy freeze]].

---

## Продукт в одном абзаце (legacy)

**trading_bot v1** — монолитный Python REPL: модули `Strategy` / `Driver` / `Info` через наследование и `eval`, SQLite-конфиг, polling свечей в стратегии.

---

## Структура (корень репо, as-is)

```
trading_bot/
├── UI.py                 # REPL, eval dispatch
├── structy.py            # Module, Strat, Driver, Info
├── database.py           # SQLite main_db + optimiser
├── support_tools.py      # Tools, Functionality, Time
├── get_finance_data.py   # Yahoo, AlphaVantage, MOEX, Finam (монолит)
├── metrics.py            # P&L, MACD, mplfinance
├── optimizer.py          # Перебор optimization_parameters
├── create_config.py      # settings.ini generator
├── settings.ini          # accounts, API keys, default driver
├── Drivers/
│   ├── Quik.py           # ★ основной (~416 строк)
│   ├── TestDriver.py     # mock
│   ├── metatrader.py     # stub
│   └── transaq.py        # stub + hardcoded creds
├── Strategies/
│   └── test_strategy.py  # единственная стратегия
├── Info/
│   └── alphavantage.py
├── External_libs/
│   └── QuikPy.py         # cia76/QuikPy
└── requirements.txt
```

**~14 файлов, ~1500 строк** (без `.venv`).

---

## Ключевые классы

### structy.py

| Класс | Роль | Проблемы |
|-------|------|----------|
| `Module` | Базовый: path, my_name, my_type | `get_attr` через eval, не работает |
| `Strat` | Стратегия: тянет Driver из DB | Tight coupling, eval для candles/portfolio/DOM |
| `Driver` | Брокер | OK как идея, плохо как база |
| `Info` | Данные | Дублируется get_finance_data |

### database.py

- `main_db`: category, parameter, state — mirror `settings.ini` + runtime overrides
- `optimiser`: задел под транзакции оптимизатора (недоделан)
- `refresh_db()` **дропает таблицы** при init — осторожно

### UI.py

- Команды: `use`, `show`, `help`, `exit`, `run`, `set`, `optimize`
- Dispatch: `eval('self.' + mod + '(command[1:])')`
- `show options` — парсит `optimization_parameters` / `default` из файла стратегии

### test_strategy.py

```python
while True:
    candles = asyncio.create_task(self.candles_stream(...))
    res = await asyncio.gather(candles)
    if res:
        self.set_transaction('', self.optimise)
```

Pull-модель, бесконечный цикл, транзакция-заглушка.

---

## Drivers — что переиспользовать

| Файл | Переносим в target | Выбросить |
|------|-------------------|-----------|
| `Quik.py` | QuikPy calls, callbacks, candle normalization | eval paths, class-level config |
| `TestDriver.py` | Fake OHLCV shapes | hardcoded tickers in get_DOM |
| `metatrader.py` | MT5 init pattern | — |
| `transaq.py` | ctypes pattern | hardcoded login/password/paths |

---

## Что работает (не ломать)

- QuikPy + локальный QUIK (candles, DOM, transactions — по мере реализации)
- TestDriver offline debug
- metrics.calc_profit — расчёт P&L по истории сделок
- optimizer — перебор комбинаций params (хрупко, но работает)
- REPL navigation: show/use/help

---

## Антипаттерны (не тащить в target)

1. **`eval()`** — UI, structy, optimizer, support_tools
2. **Strategy inherits broker access** — `Strat.driver`, `base_driver` from SQL
3. **parse_file** — чтение Python-файлов как текста для metadata
4. **Polling** — `while True` + `candles_stream`
5. **Secrets in repo** — API key in settings.ini, transaq credentials
6. **async без await** — structy async methods call sync driver via eval

---

## Git history (контекст)

| Период | Активность |
|--------|------------|
| 2022-12 | Initial upload |
| 2023-01 – 2023-04 | metrics, UI, DB, Quik, optimizer |
| 2023-07 – 2023-08 | asyncio, TestDriver, optimiser DB |
| 2024-07 | LICENCE |
| 2024-12 | refactor (последний коммит) |

---

## Миграционная карта

См. [[Карта проекта#Миграция legacy → target]] и эпики в [[TODO/README]].
