# Frontend regularization plan

The first Snake prototype exposed two frontend gaps that are useful as design signals rather than isolated bugs:

1. `SignalsExpr - Expr` failed even though `VectorScalarOp` and physical lowering already supported a whole-vector/scalar arithmetic operation.
2. `SignalsExpr.filter_eq(...)` was absent even though the semantic IR already had the generic `VectorFilter` node and the lowerer supported arbitrary comparison operators.

Both failures have the same shape: the semantic and physical layers are more regular than the Python frontend. The frontend currently exposes a hand-written subset of capabilities rather than presenting a systematic algebra whose surface matches the canonical IR.

This document proposes a small frontend-regularization milestone before adding more large examples.

## 1. Design goal

The frontend should satisfy this rule:

> If the canonical semantic IR has a simple, general operation, the frontend should either expose it systematically or explicitly document why that operation is intentionally unavailable.

The goal is not to expose every Factorio combinator feature mechanically. The goal is to make the public Python language predictable: programmers should be able to infer legal expressions from a small set of regular rules instead of discovering individual special cases.

The current data contract already states that ordinary arithmetic, comparisons, selects, lane reads, and runtime-open vector operations preserve logical occurrence. The frontend should make that semantic uniformity visible.

## 2. Clarify sparse vector semantics

A Factorio signal vector is not a dense tensor over a fixed universal index set. It is a finite signal map.

The data contract should explicitly define support behavior:

- vector/scalar operations map over the vector's current support and do not introduce absent lanes;
- vector/vector arithmetic operates over the union of the two operand supports;
- filters preserve a subset of the current support and retain original lane counts;
- lane projection returns zero for an absent lane;
- reductions such as `any()` and `max()` operate on the current finite map.

For example, if

```text
v = {A: 5, B: 2}
```

then

```text
v - 3 = {A: 2, B: -1}
```

rather than a dense result containing every other Factorio signal with value `-3`.

This rule explains both the semantic evaluator and Factorio's `Each`-style physical realization, and it prevents programmers from importing incorrect dense-tensor broadcasting intuition.

## 3. Define a small complete vector algebra

The public vector frontend should be organized around a few generic primitives.

### 3.1 Vector/vector combination

Conceptual primitive:

```python
v.combine(op, other)
```

This represents lane-wise binary operations over the union of supports and lowers to `VectorBinaryOp`.

Python operator sugar should be layered on top of this primitive when a vector/vector shape is intentionally supported.

### 3.2 Support-preserving vector/scalar map

Conceptual primitive:

```python
v.map_scalar(op, scalar)
```

This represents lane-wise arithmetic over the current support and lowers to `VectorScalarOp`.

Operations such as

```python
v * scalar
v - scalar
```

should be implemented through this path rather than as unrelated ad-hoc overloads.

### 3.3 Support filter

Conceptual primitive:

```python
v.filter(op, constant)
```

This preserves lanes satisfying the comparison while retaining their original counts and lowers to `VectorFilter`.

Convenience helpers should be thin aliases:

```python
v.filter_eq(n)
v.filter_ne(n)
v.filter_lt(n)
v.filter_le(n)
v.filter_gt(n)
v.filter_ge(n)
v.positive()      # filter_gt(0)
```

Do not overload `v == n` to mean filtering. That spelling is ambiguous between a scalar predicate, a Boolean mask, and a support-preserving filter.

### 3.4 Projection and reductions

Keep operations whose result shape genuinely changes explicit:

```python
v.signal(signal)   # vector -> scalar lane projection
v.any()            # vector -> scalar predicate
v.max()            # vector -> selected vector
```

Their semantics should be documented alongside the algebra above.

## 4. Make supported operand shapes explicit

Create one source-of-truth capability matrix for public vector operators.

For example:

```text
operation     V,V    V,S    S,V
--------------------------------
+              ?      ?      ?
-              ?      ?      ?
*              ?      ?      ?
/              ?      ?      ?
%              ?      ?      ?
<<             ?      ?      ?
>>             ?      ?      ?
&              ?      ?      ?
|              ?      ?      ?
^              ?      ?      ?
```

Every entry should be decided intentionally according to the semantic model and available lowering.

The important part is not that every cell becomes supported. Unsupported cells are acceptable when they have a clear semantic reason. What should disappear is the current accidental pattern where one operator supports a shape only because an earlier example happened to need it.

The scalar `Expr` frontend is a good model here: it centralizes binary and comparison construction in `_binary`, `_rbinary`, and `_compare`, and the public overloads are thin systematic wrappers.

`SignalsExpr` should move toward the same structure with helpers such as:

```python
_vector_binary(op, other)
_vector_scalar(op, scalar)
_filter(op, constant)
```

## 5. Add capability-matrix tests

The frontend test suite should test algebraic coverage rather than only individual examples.

Add parameterized tests that verify:

1. each advertised Python spelling accepts the documented operand shapes;
2. it constructs the expected canonical IR node (`VectorBinaryOp`, `VectorScalarOp`, or `VectorFilter`);
3. unsupported operand shapes fail with deliberate, specific `CircuitBuildError` messages;
4. every advertised vector operation survives semantic evaluation;
5. every advertised vector operation survives physical lowering.

A representative structure is:

```python
@pytest.mark.parametrize(
    ("operation", "lhs_shape", "rhs_shape", "ir_type", "ir_op"),
    [...],
)
def test_vector_operator_contract(...):
    ...
```

and separately:

```python
@pytest.mark.parametrize(
    ("method", "comparator"),
    [
        ("filter_eq", "=="),
        ("filter_ne", "!="),
        ("filter_lt", "<"),
        ("filter_le", "<="),
        ("filter_gt", ">"),
        ("filter_ge", ">="),
    ],
)
def test_vector_filter_contract(...):
    ...
```

These tests would have caught both Snake failures before the example was run in a local checkout.

## 6. Check frontend/IR/lowering symmetry

As part of the milestone, inventory these layers together:

```text
Python frontend
    -> canonical semantic IR
    -> semantic simulator
    -> physical lowerer
```

For the current vector language, inspect at least:

- `BinaryOp`
- `VectorBinaryOp`
- `VectorScalarOp`
- `VectorFilter`
- `VectorSelect`
- lane projection through `VectorSignal`

For each canonical node, record whether the frontend can construct all intended forms and whether simulator/lowering support matches.

The desired outcome is a small documented table rather than another collection of implicit assumptions.

## 7. Treat `Circuit.step()` as migration debt

Snake also highlights a separate frontend inconsistency:

```python
value.step(n)
```

means flow-local logical occurrence reindexing, while

```python
circuit.step(n)
```

moves a global compatibility observation cursor.

The distinction is documented, but the shared spelling encourages the wrong mental model and conflicts with the direction of clocked Flow semantics.

For new code, prefer local temporal expressions. The desired future style is closer to:

```python
next_value = register.sample().step(1)
```

or a dedicated state spelling such as:

```python
next_value = register.next()
```

rather than:

```python
circuit.step(1)
next_value = register.sample()
```

Do not remove `Circuit.step()` immediately because existing Level/state examples still depend on it. Instead:

1. document it explicitly as compatibility behavior;
2. stop introducing it in new frontend examples where a local spelling is possible;
3. identify the missing local state-observation API needed to migrate existing examples;
4. deprecate the global cursor only after equivalent local semantics are available and covered by tests.

## 8. Naming direction

`SignalsExpr` is historically tied to Factorio's circuit-network representation. Once the algebra is stabilized, consider whether `VectorExpr` better communicates the source-language abstraction.

A clean long-term mental model would be:

```text
ScalarExpr
    ordinary scalar payload algebra

VectorExpr
    finite sparse signal-map algebra

Flow
    payload shape + modality + clock + occurrence offset

State
    atomic transition system producing scalar/vector observations
```

This rename is not required for the regularization milestone and should not be combined with it unless it materially simplifies the refactor.

## 9. Proposed implementation sequence

### Stage A — contract

- Add the sparse-vector support rules to `docs/data-contract.md`.
- Add a compact operand-shape/operator capability table.
- Decide intentionally which vector/vector and vector/scalar arithmetic operators are public.

### Stage B — frontend refactor

- Centralize vector/vector construction in one helper.
- Centralize vector/scalar construction in one helper.
- Centralize filters in one helper.
- Reimplement existing public sugar (`positive`, filter helpers, arithmetic overloads) through those primitives.
- Improve error messages so unsupported shape combinations explain the expected algebra.

### Stage C — contract tests

- Add parameterized frontend -> IR capability tests.
- Add semantic-simulator checks for every supported vector operation.
- Add physical-lowering checks for every supported vector operation.
- Add negative tests for intentionally unsupported operand shapes.

### Stage D — temporal frontend cleanup

- Define the desired local spelling for post-transition state observation.
- Migrate new examples away from `Circuit.step()` where possible.
- Keep `Circuit.step()` as a documented compatibility layer until migration is complete.

## 10. Acceptance criteria

The milestone is complete when:

- the sparse support semantics are written in the data contract;
- the supported vector operand-shape matrix is explicit;
- every supported vector operation has a systematic frontend construction path;
- every supported vector operation is covered through frontend, semantic simulation, and physical lowering;
- unsupported combinations fail intentionally rather than through missing Python attributes or accidental type errors;
- new examples can be written without guessing whether an obvious operation exists;
- `Circuit.step()` has a documented migration path toward local clock/state expressions.

The key lesson from Snake is that a stress example should test the language, not only the backend. The backend already contained a more coherent vector algebra than the frontend exposed. The next frontend milestone should make the Python language reflect that coherence directly.