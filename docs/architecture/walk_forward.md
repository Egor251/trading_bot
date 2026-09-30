# Walk-forward research

**Status:** implemented (2026-08-02)  
**Code:** [`engine/walk_forward.py`](../../engine/walk_forward.py), [`services/phantom_service`](../../services/phantom_service/__init__.py)

## Role

Reproducible, configurable walk-forward evaluation of strategy parameter grids.
Strategies emit decisions offline via SDK `evaluate_bars`. Fill size follows
phantom/paper `SizingConfig` (default `percent_of_equity=10%`), not a fixed
`quantity=1` coin — otherwise BTC≈60k pays ~`fee_bps` on full-coin notional
(~31.5 at 5 bps) and every strategy looks uniformly bankrupt.

```
YAML walk_forward / UI / AI
        ↓
PhantomService.run_walk_forward  (sizing → qty, fee+slippage → cost_bps, timeframe)
        ↓
WalkForwardConfig → run_walk_forward_offline
        ↓
provenance + OOS summary + baselines + stable_family (+ optional JSON report)
```

## Feed semantics

| Feed | Meaning | Compare strategies? |
|------|---------|---------------------|
| `history` (default) | Real OHLC from `historical_data_service` | **Yes** |
| `patterns` | Synthetic `sharp_rise` / etc. with `seed` | No — synthetic only |

Do **not** mix patterns-WF conclusions with live phantom / consilium results.

## Timeframe (critical)

Default **`timeframe=1d`**. Crypto `historical_data_service` without an explicit
TF would otherwise use `intraday_timeframe=1m`, so a 500-bar WF was ~8 hours of
noise — every strategy looked dead and most OOS folds had zero trades.

**Integrity:** requested TF is not a label. After fetch, the service verifies the
median bar gap (`bar_interval_sec`) against the expected interval (~86400s for
`1d`). Chart **runtime** buffers are typically 1m — they are **resampled** to the
requested TF (or raise `timeframe_mismatch`). Do not trust provenance `timeframe`
alone; also check `bar_interval_sec` ≈ `expected_interval_sec`.

Auto provider resolves compact crypto tickers (`BTCUSDT`) to **ccxt** (native
daily bars). Prefer `provider=ccxt` for research; runtime 1m→1d needs many source
bars and is a fallback, not the primary research path.

Provenance records `timeframe`, `provider_resolved`, `bar_interval_sec`,
`expected_interval_sec`. Pass `1m` / `1h` only when you intentionally probe that
horizon.

**Costs:** keep realistic crypto costs (~5–7 bps round-trip class). Do not cut
fees to “make OOS green” until TF integrity is verified — that confounds edge
with accounting.
## Knobs (`WalkForwardConfig`)

| Key | Default | Notes |
|-----|---------|-------|
| `feed` | `history` | |
| `timeframe` | `1d` | History OHLC TF |
| `seed` | `42` | Patterns + bootstrap RNG |
| `bars` | `500` | History limit / pattern length |
| `train_bars` / `test_bars` | `60` / `20` | |
| `step` | `= test_bars` | Rolling stride |
| `min_folds` | `5` | Fails with `walk_forward_too_short` if series too short |
| `auto_fit_folds` | `false` | If true, shrinks step to hit `min_folds` |
| `selection_mode` | `top_k_stable` | or `best` |
| `top_k` | `3` | Stable family size |
| `include_baselines` | `true` | buy-hold / fee=0 / fixed-default |
| `bootstrap_samples` / `bootstrap_ci` | `1000` / `0.95` | CI on mean OOS PnL |
| `persist_report` | `true` | Writes under `data/phantom/walk_forward/` |

YAML: `services.phantom_service.walk_forward`. UI (DPG mode Walk-forward) and
`phantom_walk_forward` AI tool override the same keys.

**Sizing / costs (not WalkForwardConfig):** phantom `sizing` + `fee_bps` /
`slippage_bps`. Optional explicit `quantity` overrides sizing. Provenance
records `quantity`, `notional`, `cost_bps`, `sizing`, `timeframe`.

ATR sizing uses a **causal** `atr_series` (no full-series lookahead).

## Baselines (in report)

When `include_baselines=true`, each fold and the summary include:

| Baseline | Meaning |
|----------|---------|
| `buy_hold` | Long from first OOS bar → mark at fold end (same size/fees) |
| `fee_zero_selected` | Same selected config, `fee_bps=0` |
| `fixed_default` | Schema defaults `{}`, no train selection |
| `edge_vs_buy_hold` | mean OOS − buy-hold mean |
| `cost_drag` | fee0 mean − selected mean |

Use these to separate “no edge” from “selection regret” and “costs only”.

## Parameter search (optimization)

| Mode | Where | OOS? | Typical size |
|------|-------|------|--------------|
| `grid` | WF / PHANTOM_RUN | WF yes / run no | ~3 presets |
| `optimize` | WF / PHANTOM_RUN | **WF yes** / run in-sample | up to `max_variants` (default 36) |

**Effective optimization** = `phantom_walk_forward` with `variant_mode=optimize`
(dense train selection + OOS scoring). UI Walk-forward defaults to this.
`phantom_run mode=optimize` ranks the same dense space on the full window
(in-sample) — useful for smoke checks, not for claiming edge.

Unknown strategies without a hand-tuned grid fall back to a truncated cartesian
product over numeric `CONFIG_SCHEMA` min/max fields.

Provenance records `variant_mode`, `variants_count`, `max_variants`.

### Holdout / robustness

`holdout_bars=N` reserves the last N bars. WF runs on the prefix only; `chosen`
is then scored once on the untouched holdout. Report fields: `holdout.pnl`,
`digest.robust_sign_match` (same sign as mean OOS).

Successful holdout digests are recorded under `data/phantom/research_clearance.json`.

### Live readiness grades

`evaluate_live_readiness` / policy `trading.live_readiness` scores each strategy:

| grade | meaning |
|-------|---------|
| `research_only` | no usable holdout / failed observe checks |
| `observe_ok` | robust holdout on ≥1 ticker (CI may still cross zero) |
| `live_ok` | CI excludes zero, min holdout/OOS trades, PF, ≥2 robust tickers, chosen params applied |

**Live Consilium promotion** (`SET_ENSEMBLE` / `set_ensemble_lineup`) rejects new
`strategies` members unless `live_ok`, or `confirm_live_without_holdout=true`.
On successful promote, `canary_quantity_scale` (default 0.25) shrinks plugin quantity.

Defaults (override in YAML): `min_holdout_trades=8`, `min_oos_trades=20`,
`min_profit_factor=1.15`, `min_tickers_robust=2`, `require_ci_excludes_zero=true`.

### Batch + apply + multi-ticker

- `phantom_walk_forward_batch` / `PHANTOM_WALK_FORWARD_BATCH` — many strategies
  × optional `tickers=[...]`, ranked `digest` rows.
- `phantom_apply_chosen` / `PHANTOM_APPLY_CHOSEN` — write last `chosen` into
  plugin config (+ YAML persist) and sync research ticker.
- Phantom UI: **Batch WF**, digest table, **Apply chosen**, extra tickers field.

### OHLC disk cache

`historical_data_service` caches remote bars under `data/ohlc_cache/`
(`disk_cache_ttl_sec` default 1 day) so batch WF does not re-hit ccxt for the
same (provider, ticker, timeframe, limit) key.

### AI narrative reports

Machine WF JSON lives in `data/phantom/walk_forward/`. The analyst/operator can also
write **human-readable research packs** via tools:

- Short one-shot: `write_research_report` (keep `body_markdown` small)
- Long write-ups: `begin_research_report` → `append_research_report` (chunks) →
  `finalize_research_report` (conclusions + optional last WF/batch attachments)
- `list_research_reports` / `read_research_report`
- `list_walk_forward_reports` — index existing WF JSON to cite

Files land in `data/ai_reports/<stamp>_<slug>/`. Analyst-safe (no trading state change).
Truncated tool JSON is repaired best-effort in Code_base `ai_analyst`; prefer chunked
writes for deep-dives.

## Selection

- **best** — per fold: max train PnL config → OOS; summary “chosen” = most frequent best.
- **top_k_stable** — per fold keep top-k train configs; rank by fold wins, mean OOS, −std OOS → `stable_family` + `chosen`.

## Provenance

Every report includes `provenance`: feed, seed, bars, train/test/step, folds_realized,
selection_mode, strategy_id, ticker, class_code, source, **timeframe**,
**provider_resolved**, **bar_interval_sec**, **expected_interval_sec**,
**variant_mode**, **variants_count**, quantity,
notional, cost_bps, sizing, position_mode, generated_at.

Open positions at fold end are marked to the **last close of the window**
(`equity_pnl(..., mark_price=closes[-1])`). Marking only at the entry fill
price previously zeroed unrealized PnL (OOS ≈ −fee for a single open), which
made every strategy look uniformly fee-bankrupt on short test folds.

OOS folds also **carry** the active BUY/SELL side from the train prefix into
the test window (fee-free accounting open at the last train close). Without
carry, always-in-market strategies that do not flip inside a 20-bar test
scored `pnl=0` and walk-forward only saw fee-paying churn — another systematic
short bias. Carry is cleared if the prefix ends in ``HOLD`` + ``intent=flat``
(true exits only). Mean-reversion / pulse neutrals must use ``neutral_hold`` /
``maintain_side_hold`` (``intent=long|short``) so research matches live
(``emit_on_side_change``).

Default ``position_mode=long_flat`` matches paper ``forbid_net_flip``: ``SELL``
flattens and never opens a short. Pass ``net_flip`` only when shorts are allowed.

Invariant tests: `tests/test_research_invariants.py`.

## Related phantom diagnostics

- `strategy_sizing` map under `phantom_service` — per-strategy max_position / strength overrides in shadow fills.
- `get_phantom_audit` / `phantom_audit()` — rejects + round-trips from shadow ledger.
- `phantom_reset` — full wipe or scoped (`strategy_id` / `symbol` + `confirm_scoped`).

## Consilium weights

`weight_mode=risk_parity|bandit_ucb` adapts **live** consilium votes. It is not part of
offline walk-forward scoring.

## Tests

- `tests/test_research.py` — windows, WF offline, min_folds, bootstrap, reproducibility
- `tests/test_walk_forward.py` — config merge, stable family, too-short error, BTC sizing cost, baselines, timeframe
- `tests/test_ohlc_timeframe.py` — resample 1m→1d, mismatch refuse, BTCUSDT→ccxt infer
- `tests/test_phantom_variants.py` — grid/optimize spaces, schema cartesian fallback
- `tests/test_research_ops.py` — digest, apply-chosen, OOS DD/trades aggregates
