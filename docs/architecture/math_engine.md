# Math engine (cb_math)

Portable SymPy CAS used by trading_bot for formula work (diff / integrate / matrices / eval).

## Location

- Package: sibling repo `Code_base/cb_math` (extracted from Aphina `math_calculator/engine`)
- Bootstrap: [`host/bootstrap.py`](../../host/bootstrap.py) adds `Code_base/cb_math` to `sys.path`
- Trading series layer: [`engine/indicators`](../../engine/indicators) — SMA/EMA/perf + `FormulaSeries`

## Usage

```python
from cb_math import run_math_operation

run_math_operation(domain="calculus", operation="diff", expression="x**2", variable="x")
run_math_operation(domain="matrix", operation="det", expression="Matrix([[1,2],[3,4]])")
```

```python
from engine.indicators import FormulaSeries, sma

FormulaSeries("a - b").eval({"a": closes, "b": sma(closes, 5)})
FormulaSeries("x**2").derive("x")  # -> 2*x
```

Hot-path indicators stay pure Python; SymPy is for defining/deriving formulas and point eval.

## Out of scope (this note)

- Migrating Aphina plugin onto `cb_math` (can later)
- YAML indicator DSL
