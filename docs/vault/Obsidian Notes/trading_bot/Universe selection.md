# Universe Selection — чем торговать из тысяч инструментов

**Статус:** канон (решение продукта)  
**Связано:** [[Философия архитектуры]], [[Технический документ — адаптивный консилиум стратегий]], vault `Universe selection`, `Event Contract` (`universe.*`), `DATA FLOW`

> Философия продукта: **максимальная настраиваемость** и **максимальная адаптивность**.  
> Это касается и отбора бумаг: не один зашитый screener, а конфигурируемые профили + возможность менять критерий под стратегию, режим и класс актива.

---

## Короткий ответ

**Не мониторить всё в live.** Делаем воронку:

```
каталог (тысячи)  →  screen profile (сотни)  →  active book (десятки)
     дёшево           критерий настраивается      live candles / DOM / chains
```

Слой universe подчиняется той же философии, что и консилиум:

| | Настраиваемость | Адаптивность |
|--|-----------------|--------------|
| **Что** | profile / rank_by / floors / asset_classes / manual list в YAML | смена профиля, union books стратегий, позже — regime/meta → другой screen |
| **Чего нет** | «всегда top по объёму» в коде | «один список навсегда» |

**Отбор по ликвидности — не единственный и не всегда лучший критерий.**  
Это один из **профилей** (`liquid`). Vol-стратегии, фьючерсная спекуляция и опционы требуют других профилей (и иногда другой модели инструмента). Профиль выбирается конфигом и может меняться — это и есть адаптивность на уровне «чем торгуем».

Ручной whitelist (`manual`) — first-class для Stage 1 и узких рынков (настраиваемость с нуля).

---

## Почему нельзя «следить за всем»

| Ограничение | Практика |
|-------------|----------|
| Лимиты брокера / data feed | Нельзя открыть тысячи минутных подписок |
| Память / CPU | Большой universe убивает latency |
| Размер БД | Полный тик по всем рынкам дорог |
| Издержки | На неликвиде проскальзывание съедает альфу |

Вывод: **каталог — дёшево и редко**; **дорогое (live) — только active**.

---

## Главное уточнение: критерий зависит от стратегии

| Цель стратегии | Что ранжируем | Пол (floor), не цель | Профиль |
|----------------|---------------|----------------------|---------|
| Тренд / mean-rev на «торгуемых» | `dollar_volume` | min price / volume | `liquid` |
| Игра на волатильности | `atr_pct` / `realized_vol` | **всё равно** min dollar_volume (иначе не выйти) | `volatility` |
| Направленная спекуляция с плечом / низкими комиссиями | volume внутри **futures** | asset_classes=`future` | `futures_speculation` |
| Крипто-ликвидность | `dollar_volume` | crypto-only | `crypto_liquid` |
| Опционы (этап 1) | `iv_rank` по **базису** | OI / spread базиса | `options_underlying` |
| Опционы (этап 2) | chain: DTE, delta, OI, bid-ask контракта | — | отдельный chain-screener (позже) |

**Пол ≠ цель.** Vol-профиль всё равно отсекает полный мусор по ликвидности, но **сортирует по воле**, а не по объёму. Иначе «самая волатильная» бумага окажется неторгуемой.

### Акции vs фьючерсы vs крипто

Спекулировать **акциями** часто менее выгодно из‑за комиссий, налога, overnight и маржи относительно **фьючерсов** (Si, BR, RI…) или перпов. Это не баг screener’а — это **выбор asset class в профиле / профиле сборки**:

- paper/research на TQBR — ок;
- боевая направленная спекуляция — чаще `futures_speculation` или crypto;
- акции остаются для дивидендных / factor / опционных underlyings.

Конфиг это выражает через `asset_classes`, а не через «один глобальный top-500 акций».

### Опционы — другой мир

Опционная вселенная **двухуровневая**:

1. **Underlying screen** — какие базисы интересны (IV rank, realized vs implied, earnings calendar).
2. **Chain screen** — какие контракты (expiry, strike/delta, open interest, bid-ask%), затем подписка на выбранные серии + спот базиса.

Нельзя свести опционы к `top_n` по dollar volume акций. В архитектуре:

- `asset_class: option` + `meta.underlying` для контрактов;
- профиль `options_underlying` сейчас (базисы);
- chain-screener / options plugin — отдельный этап (не Stage 1).

Греки, маржа, assignment — в risk/options модуле, не в общем equity OrderManager без доработки.

---

## Модель в trading_bot

### Ярусы (общие)

| Ярус | Данные | Частота |
|------|--------|---------|
| **catalog** | метаданные + дешёвые метрики (volume, ATR%, IV rank…) | день / N часов |
| **candidates** | после floors профиля | то же |
| **active** | live для Engine | continuously |

### Профили (`universe.profile` / `screen`)

Встроенные: `liquid`, `volatility`, `futures_speculation`, `crypto_liquid`, `options_underlying`  
(`engine/universe.py` → `BUILTIN_PROFILES`).

YAML может переопределить `rank_by`, floors, `asset_classes`.

### Мульти-стратегия (консилиум)

У каждой стратегии **свой** screen → свой active.  
**Глобальный** market book = **union** (`UniverseManager.merge_active_books`), чтобы:

- trend смотрел ликвидные фьючерсы;
- vol-strategy — высоко-ATR имена;
- options — базисы с высоким IV rank;

и market service стримил объединение, а не один компромиссный top-N.

Режимы:

- `manual` — static_active / include;
- `screener` — один активный профиль (или later: multi-profile union в сервисе).

---

## «А если пропустим хорошую бумагу?»

Да. Trade-off:

- профиль ловит то, под что заточена стратегия;
- opportunity scanner (новости / аномалии) позже **номинирует** в candidates без live на весь рынок;
- `include` + pin открытых позиций защищают от «выпал из рейтинга → отписались».

---

## Реализация

| Компонент | Роль |
|-----------|------|
| `engine/universe.py` | `Instrument` (+ atr/iv/…), `ScreenPolicy`, `UniverseManager`, профили |
| `services/universe_service` | reselect, `universe.*` |
| `config/trading_bot.yaml` → `universe:` | mode, profile, screen, lists |
| Market services | только active (union) |

События: `universe.snapshot` (поля `profile`, `rank_by`), `universe.changed`.

---

## Конфиг (канон)

```yaml
universe:
  mode: manual                 # Stage 1 default
  # mode: screener
  # profile: volatility        # liquid | volatility | futures_speculation | …
  static_active:
    - TQBR:SBER
    - TQBR:GAZP
  include: []
  exclude: []
  screen:                      # overrides builtin profile
    max_active: 10
    max_candidates: 50
    # rank_by: atr_pct
    # min_dollar_volume: 500000   # floor for vol profile
    # asset_classes: [future]
    prefer_previous_active: true

  # Later: per-strategy books
  # profiles:
  #   trend: { profile: futures_speculation, max_active: 5 }
  #   vol:   { profile: volatility, max_active: 8 }

services:
  universe_service:
    enabled: true
    catalog_path: config/universe_catalog.yaml
  mock_market_service:
    follow_universe: true
```

Примеры:

```yaml
# Vol play на акциях с полом ликвидности
universe:
  mode: screener
  profile: volatility
  screen:
    asset_classes: [equity]
    max_active: 15
    min_dollar_volume: 1000000

# Спекуляция фьючерсами (комиссии / плечо)
universe:
  mode: screener
  profile: futures_speculation
  screen:
    max_active: 8
```

---

## Инварианты

1. Market service **не** live-подписывается на весь catalog.
2. **Ранг ≠ пол:** floor гарантирует исполняемость; rank отражает edge стратегии — оба **настраиваются**.
3. Класс актива выбирается профилем / профилем сборки (акции ≠ фьючерсы ≠ опционы).
4. Опционы: underlying screen сейчас; chain screen — отдельный этап.
5. Консилиум → union active books (адаптивность состава под набор стратегий).
6. Логика в `engine/universe.py`; I/O в `universe_service`.
7. **Анти-философия:** зашить один критерий отбора в код стратегии или market adapter.

---

## Рекомендация на сейчас

1. Stage 1: `mode: manual` + узкий список (в т.ч. фьючерсы, если цель — спекуляция) — настраиваемость с первого дня.
2. Для paper vol: `profile: volatility` + seed-метрики `atr_pct` в catalog.
3. Опционы не имитировать equity-скринером — заложить в roadmap отдельный chain path.
4. Когда появятся несколько стратегий — multi-profile union в `universe_service` (адаптивный состав active).
5. Дальше: смена profile по regime/meta — та же адаптивность, что у консилиума, на слое «чем торгуем».
