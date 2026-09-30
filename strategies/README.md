# Strategies

Packages here are trading strategies (`StrategyPlugin`), loaded by
`plugin_manager` via `plugins.paths: [strategies, plugins]`.

| Dir | Role |
|-----|------|
| `strategies/` | Public / example strategies (this folder) |
| `strategies_local/` | Private strategies (`plugins.installed_paths`) |
| `plugins/` | Non-strategy plugins (AI, execution, market connectors) |
| `plugins_local/` | Private non-strategy plugins |

## Catalog

| Id | Family | Idea |
|----|--------|------|
| `ma_cross_example` | Trend | SMA fast/slow cross |
| `ema_cross` | Trend | EMA 12/26-style cross |
| `macd_trend` | Momentum | MACD vs signal line |
| `momentum_roc` | Momentum | Rate-of-change threshold |
| `supertrend_follow` | Trend | ATR Supertrend trail |
| `adx_di_trend` | Trend | DI+/DI− gated by ADX |
| `parabolic_sar_follow` | Trend | Parabolic SAR flips |
| `heikin_ashi_trend` | Trend | Heikin-Ashi color streak |
| `mean_reversion_example` | Mean reversion | SMA z-score fade |
| `rsi_reversion` | Mean reversion | RSI oversold/overbought |
| `bollinger_reversion` | Mean reversion | Bollinger band fade |
| `stochastic_reversion` | Mean reversion | Stochastic %K/%D fade |
| `cci_reversion` | Mean reversion | CCI extremes fade |
| `williams_r_reversion` | Mean reversion | Williams %R extremes |
| `vwap_reversion` | Mean reversion | Rolling VWAP deviation |
| `volatility_breakout_example` | Breakout | Donchian + ATR buffer |
| `keltner_breakout` | Breakout | Keltner EMA±ATR channel |
| `turtle_breakout` | Breakout | Turtle N/M Donchian |
| `dual_thrust` | Breakout | Open ± k·range |
| `volume_impulse_example` | Microstructure | Volume spike + return |
| `test_strategy` | Dev | Fixture / REPL smoke |

Do not put market adapters or actuators here.
