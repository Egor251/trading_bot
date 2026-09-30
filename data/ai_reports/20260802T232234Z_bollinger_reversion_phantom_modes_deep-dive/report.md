# bollinger_reversion phantom modes deep-dive

BTCUSDT 1d seed=42: grid negative, dense optimize positive, holdout robust_sign_match with chosen window=14 num_std=1.5. CI still crosses zero; not automatic live.

**kind**: `walk_forward` | **generated_at**: `20260802T232234Z` | **author**: `ai_analyst` | **strategies**: `bollinger_reversion` | **tickers**: `BTCUSDT` | **tags**: `bollinger`, `holdout`, `btc`, `optimize`, `grid`

## Metrics

| key | value |
|---|---|
| `grid_mean_oos_pnl` | -1.95 |
| `optimize_mean_oos_pnl` | 10.14 |
| `holdout_wf_mean_oos_pnl` | 8.22 |
| `holdout_pnl` | 54.09 |
| `holdout_trades` | 3 |
| `robust_sign_match` | true |
| `ci_crosses_zero` | true |
| `chosen_window` | 14 |
| `chosen_num_std` | 1.5 |
| `bar_interval_sec` | 86400 |
| `provider_resolved` | ccxt |
| `variants_optimize` | 20 |

## Mode table

| mode | variants | mean OOS | CI 95% | win-rate | PF | edge_vs_bh | cost_drag |
|---|---:|---:|---|---:|---:|---:|---:|
| grid | 3 | -1.95 | [-32.5, +25.6] | 40.9% | 0.92 | +17.3 | 0.52 |
| optimize | 20 | +10.14 | [-22.9, +38.8] | 54.5% | 1.43 | +29.4 | 0.49 |
| optimize+holdout60 | 20 | +8.22 | [-27.9, +42.3] | 57.9% | 1.30 | +29.2 | 0.53 |

## Caveats

- CI crosses zero in all modes → statistical significance weak.
- Holdout +54.09 on only 3 trades / 60 bars → sign match is encouraging, not sufficient for confident live sizing.
- Clearance recorded for bollinger_reversion::BTCUSDT only.
- YAML previously had window=10/num_std=2.5; holdout chosen is 14/1.5.

## Narrative

## Findings
1. Grid (3-point) understates bollinger (-1.95); dense optimize finds +10.14 — grid is not enough for param search.
2. Best research mode is optimize (+10.14, WR 54.5%, PF 1.43).
3. Holdout (60 bars) keeps positive mean OOS (+8.22) and robust_sign_match=true with holdout.pnl=+54.09.
4. Provenance OK: bar_interval_sec=86400, provider_resolved=ccxt, long_flat, forbid_net_flip, variants_count=20.

## Decision
Candidate for observe / paper with chosen params on BTC only. Live votes deferred until CI/trade-count improve or multi-ticker holdout agrees.

## Conclusions

- Dense optimize required; do not trust grid for bollinger.
- Holdout sign match on BTCUSDT clearance is recorded.
- Not live-ready solely on this: CI crosses zero; holdout n_trades=3.

## Next steps

- Apply chosen window=14 num_std=1.5; keep observe_only on BTC/USDT.
- Optional: holdout batch on ETH/SOL before any live lineup add.
- Shadow/observe for trade count before promoting to live votes.

## Attachments

- `clearance_snapshot` → `data/ai_reports/20260802T232234Z_bollinger_reversion_phantom_modes_deep-dive/attachments/clearance_snapshot.json`
