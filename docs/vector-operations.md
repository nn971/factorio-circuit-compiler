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
left.any()
left.gate(condition)
```

Addition and subtraction are lane-wise. Missing lanes contribute zero, and lanes whose result is zero
disappear from the sparse map. Scalar multiplication applies the same scalar to every present lane.
`positive()` preserves the original count of every lane whose count is greater than zero. `any()` is a
scalar `0/1` predicate for whether the vector contains at least one nonzero lane. `gate(condition)`
passes the vector when `condition` is nonzero and otherwise produces the empty vector.

## Deficit example

A compact use case is comparing a requested item vector with current stock:

```python
from factorio_circuit import compile_circuit
from factorio_circuit.frontend import Circuit

circuit = Circuit("vector_deficit")
required = circuit.signals("required")
stock = circuit.signals("stock")
enabled = circuit.input("enabled")

missing = (required - stock).positive()

circuit.output("missing", missing)
circuit.output("has_missing", missing.any())
circuit.output("enabled_missing", missing.gate(enabled))

result = compile_circuit(circuit)
print(result.blueprint_string)
```

For example, if `required` contains `iron=9, copper=2` and `stock` contains `iron=4, copper=5`,
`missing` contains only `iron=5`.

The target lowering uses Factorio's `Each` arithmetic for lane-wise arithmetic, `Each > 0 -> Each`
with input-count copying for `positive()`, and `Anything != 0` for `any()`. Every arithmetic or
decider stage adds one physical tick, with ordinary phase alignment inserted when operands arrive at
different phases.

This milestone intentionally covers stateless vector algebra. Applying these operations to
state-derived vectors remains part of the next state-timing milestone.
