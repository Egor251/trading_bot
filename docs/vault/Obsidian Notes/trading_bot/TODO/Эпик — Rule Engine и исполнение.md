# Эпик — Rule Engine и исполнение

**Приоритет:** P1  
**Статус:** каркас (consilium path)  
**Связано:** [[Event Contract]], [[TODO/Эпик — Strategies → Plugins]]  
**Пакет:** `Code_base/cb_rule_engine` (не внутри microkernel)

---

## Цель

```
strategy.signal → ensemble_service → ensemble.decision
       → RuleEngine (checks) → order_execution → paper/quik
```

---

## Сделано

- [x] `plugins/order_execution` TRIGGERED + paper/quik
- [x] `data/rules/paper_consilium_on_decision.xml`
- [x] `ensemble_service` check `is_trade_decision`; `route_execution: false` по умолчанию
- [x] Direct `strategy.signal` → execution suppressed when ensemble enabled
- [x] Boot order: construct RuleEngine before plugin `on_initialize` (register_check)

## Дальше

- [ ] Risk checks: `within_risk_limits`, `market_hours`
- [ ] Live QUIK confirm flag

---

## Не в scope

- Smart order routing
- Multi-leg orders
