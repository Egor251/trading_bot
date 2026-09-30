# Position sizing

**Status:** implemented (2026-07-31)  
**Code:** [`engine/position_sizing.py`](../../engine/position_sizing.py)

## Role

Sizing lives **outside** strategy decisions. Strategies emit `action` + `strength` (0..1); quantity is computed from equity, mark price, and `SizingConfig`.

```
StrategyDecision → signal (qty ignored / hint only)
                 → PositionSizer(equity, price, strength, atr?)
                 → OrderManager / phantom shadow fill
```

## Consumers

| Path | Where |
|------|--------|
| Phantom | `PhantomService._shadow_fill` — always sizes before submit (`apply_sizing=False` on runtime to avoid double-apply) |
| Paper / live | `TradingRuntime.submit_signal` / `submit_signal_async` when `runtime.sizing_config` is set |
| Reduce-only / exits | **not** resized |

## Modes (`SizingMode`)

| Mode | `value` meaning | Formula (simplified) |
|------|-----------------|----------------------|
| `fixed` | absolute qty | `qty = value` (or signal qty hint) |
| `cash` | notional in account currency | `qty = value / price` |
| `percent_of_equity` | % of equity | `qty = (equity × value/100) / price` |
| `risk_percent` | % equity to risk | `qty = (equity × value/100) / (price × stop_pct)` |
| `atr_risk` | % equity to risk | `qty = (equity × value/100) / (ATR × atr_mult)` |
| `kelly` | scale of Kelly fraction (100 = full computed f) | `f* = W − (1−W)/R`; `qty = equity × f* × kelly_fraction × (value/100) / price` |

Defaults for Phantom: `percent_of_equity = 10`, `start_equity = 10000`, `max_position_pct = 0.25`.

## Strength

- `scale_by_strength` (default ON): `qty *= strength` when strength is provided
- Missing / NaN strength → factor `1.0` (manual / operator paths)
- `strength_floor` (default `0.25`): **reject** when `0 ≤ strength < floor` (qty = 0).
  Does **not** boost weak signals up to the floor.
- Timeline / risk.reject reason: `strength_below_floor=…<…`
- Example: `ma_cross_example` strength = relative MA spread vs ~1% of price (not constant 1.0)

## Config

**Phantom** (`services.phantom_service` in `config/trading_bot.yaml` and Operator Run UI):

- `sizing_mode`, `sizing_value`, `stop_pct`, `atr_period`, `atr_mult`
- `win_rate`, `reward_risk`, `kelly_fraction`
- `max_position_pct`, `scale_by_strength`, `strength_floor`
- `start_equity`, `fee_bps`, `slippage_bps`

**Live/paper** (`trading.sizing` mapping → `SizingConfig.from_dict` in `TradingRuntime.from_config`).

`PHANTOM_RUN` / `run_grid(..., sizing=…, strategy_params=…)` accept overrides per run.

## Operator UI

Phantom Run zone:

1. Risk / Sizing knobs (mode-dependent fields shown/hidden)
2. Strategy params from `CONFIG_SCHEMA` / bridge `strategy_schemas`
3. Patterns / feed / grid mode as before

## Tests

- `tests/test_position_sizing.py` — modes, strength, Kelly/ATR, runtime paper submit
- `tests/test_phantom_sizing.py` — shadow fill notional overrides plugin `quantity=0.001`
- `tests/test_phantom_ux.py` — UI percent → fraction conversion for collect_run

## Out of scope (for now)

- Auto Kelly from trade history (manual W/R knobs)
- Dedicated Desk live sizing panel (use yaml `trading.sizing`)
