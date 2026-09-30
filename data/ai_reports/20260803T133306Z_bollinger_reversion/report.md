# bollinger_reversion — все режимы фантома

Полный прогон bollinger_reversion на всех режимах фантома (grid, optimize, optimize+holdout) на BTCUSDT 1d. Dense-поиск находит edge, holdout подтверждает робастность (robust_sign_match=true), но live-readiness блокируется (CI через ноль, мало сделок, 1 тикер).

**kind**: `walk_forward` | **generated_at**: `20260803T133306Z` | **author**: `ai_analyst` | **strategies**: `bollinger_reversion` | **tickers**: `BTCUSDT`

## Metrics

| key | value |
|---|---|
| `grid_mean_oos_pnl` | -1.95 |
| `grid_win_rate` | 0.409 |
| `grid_pf` | 0.921 |
| `optimize_mean_oos_pnl` | 10.14 |
| `optimize_win_rate` | 0.545 |
| `optimize_pf` | 1.429 |
| `holdout_mean_oos_pnl` | 8.22 |
| `holdout_win_rate` | 0.579 |
| `holdout_pf` | 1.3 |
| `holdout_pnl` | 54.09 |
| `robust_sign_match` | true |
| `live_grade` | research_only |
| `live_ok` | false |
| `observe_ok` | false |

## Narrative

_(draft — append sections next)_
