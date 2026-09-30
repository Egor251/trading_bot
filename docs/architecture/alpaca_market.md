# Alpaca market data (alpaca_market_service)

Репозиторий [alpaca-trade-api-python](https://github.com/alpacahq/alpaca-trade-api-python) **deprecated**.
В проекте используется актуальный SDK **`alpaca-py`** (тот же Alpaca Market Data API).

## Setup (optional — off by default)

Prefer CCXT/Binance for crypto live; see [`market_adapters.md`](market_adapters.md).

```powershell
python -m pip install "alpaca-py>=0.40"

# Paper keys from https://app.alpaca.markets/ (API Keys)
$env:ALPACA_API_KEY = "PK..."
$env:ALPACA_SECRET_KEY = "..."
# aliases also accepted: APCA_API_KEY_ID / APCA_API_SECRET_KEY
```

## Config (`config/trading_bot.yaml`)

- `services.mock_market_service.enabled: false`
- `services.ccxt_market_service.enabled: false` (disable crypto feed if switching)
- `services.alpaca_market_service.enabled: true`
- `universe.static_active: [US:AAPL, US:MSFT, US:SPY]`

## Run

```powershell
python host/main.py --headless
python -m ui
```

Market feed: IEX (free). SIP requires a paid Alpaca data plan (`feed: sip`).
