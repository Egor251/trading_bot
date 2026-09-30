# Private strategies

Drop private `StrategyPlugin` packages here (each with `manifest.yaml`).

Listed in `config/trading_bot.yaml` as:

```yaml
plugins:
  installed_paths:
    - strategies_local
    - plugins_local
```

Public examples live in `strategies/`. Non-strategy plugins go in
`plugins/` / `plugins_local/`.
