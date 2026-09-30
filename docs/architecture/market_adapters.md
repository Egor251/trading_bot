# Market adapters — универсальный порт к биржам

**Статус:** каркас (2026-07)  
**Код:** `engine/market.py`, `engine/adapters/`, `plugins/market/*_market_service`, `plugins/market/market_adapter_base.py`, `plugins/market/historical_data_service`

---

## Идея

Биржа подключается **адаптером**. Задача драйвера — привести сырые данные к **Event Contract** (`market.candle`, …).  
Стратегии и risk не знают QUIK/Binance/CCXT.

```text
universe.active → MarketAdapterServiceBase → MarketDataPort → publish_market_candle
historical_data_service → Yahoo/MOEX adapter → publish_market_history_candle
```

| Слой | Роль |
|------|------|
| `MarketDataPort` | `connect` / `fetch_ohlcv` / `stream_candles` |
| `BrokerExecutionPort` | `place_order` (задел под live) |
| `candle_to_market_payload` | Candle → payload `market.candle` / history |
| `MarketAdapterServiceBase` | follow_universe + poll loop + publish |
| Адаптеры | alpaca / mock / ccxt / quik(+transport) / yahoo / moex |

---

## Адаптеры

| Id | Service | Deps | Default |
|----|---------|------|---------|
| **ccxt** | `ccxt_market_service` | `pip install ccxt` (+ `ccxt.pro` watch) | **on** (**Bitget** spot; paper fills) |
| yahoo | `historical_data_service` | `yfinance` | **on** (on-demand history) |
| moex | `historical_data_service` | `aiomoex` + `aiohttp` | optional (RU equities) |
| mock | `mock_market_service` | — | off (smoke/fallback) |
| alpaca | `alpaca_market_service` | `alpaca-py` + API keys | off (optional US stocks) |
| quik | `quik_market_service` | `QuikTransport` impl | off |

Live stream ≠ history: история публикует `market.history.candle` (не двигает paper path).

Символы: внутренний ключ часто `class_code:ticker` (`CRYPTO:BTC/USDT`); CCXT нормализует к `BTC/USDT`. Yahoo TQBR → `SBER.ME`. Маппинг — ответственность адаптера/сервиса.

### Bitget (paper simulation)

Котировки с Bitget через CCXT; исполнение — `trading.mode: paper` (симуляция, не реальные ордера на бирже).

```yaml
trading:
  mode: paper
  venue_by_class:
    CRYPTO: bitget
services:
  ccxt_market_service:
    enabled: true
    auto_start: true
    exchange: bitget
    api_key_env: BITGET_API_KEY
    secret_env: BITGET_SECRET
    password_env: BITGET_PASSWORD   # API passphrase
    tickers: [BTC/USDT, ETH/USDT, SOL/USDT]
```

```env
BITGET_API_KEY=...
BITGET_SECRET=...
BITGET_PASSWORD=...   # passphrase from Bitget API key form
```

Публичный OHLCV часто работает и без ключей; ключи нужны для приватных вызовов / будущего live.

### Быстрый старт (crypto)

```yaml
services:
  alpaca_market_service:
    enabled: false
  ccxt_market_service:
    enabled: true
    auto_start: true
    exchange: bitget
    interval_sec: 5
    use_watch: true   # websocket via ccxt.pro when available; else REST poll
    tickers: [BTC/USDT, ETH/USDT, SOL/USDT]
  historical_data_service:
    enabled: true
    default_provider: auto
universe:
  static_active: [CRYPTO:BTC/USDT, CRYPTO:ETH/USDT, CRYPTO:SOL/USDT]
```

```powershell
python -m pip install "ccxt>=4.0"
python host/main.py --headless
python -m ui
```

Alpaca details (optional): [`alpaca_market.md`](alpaca_market.md).

---

## QUIK без QuikPy

Legacy QUIK/QuikPy (ранее `Drivers/`, тег `legacy-v1`) не канон.

Порт: [`engine/quik_transport.py`](../engine/quik_transport.py) — `QuikTransport`.  
Factory: [`engine/quik_transports/`](../engine/quik_transports/) — см. ADR [`quik_transport_spike.md`](quik_transport_spike.md).

| Кандидат | Статус |
|----------|--------|
| **fake** | **сделано** (CI / dry-run) |
| [Alex-Shur/quik_python](https://github.com/Alex-Shur/quik_python) | **обёртка** `quik_python` (preferred live) |
| [quik-lua-rpc](https://github.com/Enfernuz/quik-lua-rpc) + [qlua](https://pypi.org/project/qlua/) | **обёртка** `qlua_rpc` (alternate) |
| cia76 QuikPy | только legacy (`legacy-v1`) |

```yaml
quik_market_service:
  enabled: false
  transport: null   # fake | quik_python | qlua_rpc
  transport_options: {}
  follow_universe: true

historical_data_service:
  enabled: true
  default_provider: yahoo
  cache_ttl_sec: 900
```

REPL: `history SBER`, `metrics`, `show history`.

---

## Связано

- Event Contract `market.*` / `market.history.candle`
- Universe selection — стрим только active; history — метрики catalog
- Эпик Drivers → Services / источники данных
