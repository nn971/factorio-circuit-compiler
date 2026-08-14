# Whole-vector operations

`SignalsExpr` can represent a sparse Factorio signal map whose lane set is only known at runtime.
The first vector-algebra slice provides:

```python
left + right
left - right
left * scalar
scalar * left
-left
left.positive()
left.max()
left.any()
left.gate(condition)
```

Addition and subtraction are lane-wise. Missing lanes contribute zero, and lanes whose result is zero
disappear from the sparse map. Scalar multiplication applies the same scalar to every present lane.
`positive()` preserves the original count of every lane whose count is greater than zero. `max()`
returns a one-lane vector containing the nonzero signal with the greatest count and preserves that
count. Ties follow Factorio's selector-combinator ordering and should not be used as a portable
priority rule. `any()` is a scalar `0/1` predicate for whether the vector contains at least one
nonzero lane. `gate(condition)` passes the vector when `condition` is nonzero and otherwise produces
the empty vector.

## Deficit example

A compact use case is comparing a requested item vector with current stock and choosing the largest
current shortage:

```python
from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

circuit = Circuit("vector_deficit")
required = circuit.signals("required")
stock = circuit.signals("stock")
enabled = circuit.input("enabled")

missing = (required - stock).positive()
request = missing.max()

circuit.output("missing", missing)
circuit.output("request", request)
circuit.output("has_missing", missing.any())
circuit.output("enabled_missing", missing.gate(enabled))

result = compile_circuit(circuit)
print(result.blueprint_string)
```

For example, if `required` contains `iron=9, copper=2` and `stock` contains `iron=4, copper=5`,
`missing` and `request` both contain only `iron=5`. With several shortages, `request` contains only
the one with the greatest shortage count.

The target lowering uses Factorio's `Each` arithmetic for lane-wise arithmetic, `Each > 0 -> Each`
with input-count copying for `positive()`, a selector combinator in select-max/index-0 mode for
`max()`, and `Anything != 0` for `any()`. Every arithmetic, decider, or selector stage adds one
physical tick, with ordinary phase alignment inserted when operands arrive at different phases.

This milestone intentionally covers stateless vector algebra. Applying these operations to
state-derived vectors remains part of the next state-timing milestone.
