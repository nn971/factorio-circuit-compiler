# Factorio Circuit Compiler

Experimental compiler from a symbolic Python EDSL to optimized Factorio 2.x combinator circuits and
blueprints.

The compiler keeps logical clock/stream semantics separate from physical combinator timing. Both the
ordinary Level/state path and the implemented clocked Event path now lower through Abstract Physical
IR, physical synthesis, layout, and blueprint serialization.

```text
ordinary Python elaboration
        ↓
semantic CircuitModule
  - clocked scalar/vector flows
  - logical occurrence offsets
  - explicit state transitions
  - explicit clock crossings
        ↓
causality + clock/timing analysis
        ↓
AbstractPhysicalCircuit
        ↓
physical synthesis + Layout
        ↓
blueprint JSON/string
```

Start with:

- [`docs/HANDOFF.md`](docs/HANDOFF.md)
- [`docs/semantics.md`](docs/semantics.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/state-design.md`](docs/state-design.md)
- [`docs/clocked-flow-milestone-closeout.md`](docs/clocked-flow-milestone-closeout.md)

## Symbolic frontend

```python
from factorio_circuit import Circuit, compile_circuit

c = Circuit("controller")
a = c.input("a")
b = c.input("b")
limit = c.input("limit")

total = (a + b) * 3
result = (total > limit).select(limit, total)

c.output("result", result)
compiled = compile_circuit(c)
```

Python execution is elaboration. Symbolic values describe logical streams; physical phases remain a
compiler concern.

## Clocked flows and logical stepping

Every canonical flow carries a clock and a logical occurrence offset. Expression `.step(n)` is pure
occurrence reindexing:

```python
future_occurrence = flow.step(1)
```

For periodic/Level state, `Circuit.step()` remains a compatibility cursor. It is no longer the
fundamental semantic representation. `Circuit.tick()` is reserved for future explicit physical
scheduling.

Inputs and registers use `.sample()` for Level observation. `register.value` remains a deprecated
compatibility alias.

## Event clocks

An external Event is represented physically as a payload path plus a one-tick activation/valid path.
For an Event named `source`, the generated physical ABI contains:

```text
source          payload
source__valid   occurrence pulse
```

Scalar and vector Events are supported. Event payload value and Event presence are distinct, so a
zero scalar or empty vector can still be a present occurrence.

```python
from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy

c = Circuit("event_example")
source = c.signal_event("source", guaranteed_min_separation=4)
tick = c.event("tick", guaranteed_min_separation=5)

window = c.sum_into(source, tick)
c.output("window", window, policy=OutputMaterializationPolicy.VALID)

compiled = compile_circuit(c)
```

The compiler derives recurrence/bridge timing requirements and checks them against external Event
minimum-separation guarantees. Logical causality errors and physical throughput errors are reported
separately.

## Explicit clock operations

The implemented clock/crossing vocabulary includes:

- `sample_on(level, event_clock)` — observe Level data at Event occurrences;
- `gate_clock(parent, when=...)` — derive a subclock;
- `event_merge(...)` — additive Event union with simultaneous-occurrence coalescing;
- `hold_into(source, target)` — preserve the latest source payload for target occurrences;
- `sum_into(source, target)` — accumulate a vector Event over `(previous_target, current_target]`;
- Event `.step(n)` — suppress the first `n` occurrences and preserve the surviving payloads.

`HoldInto` uses a strict-prior simultaneous boundary. `SumInto` uses a right-closed boundary, so a
source occurrence simultaneous with the target belongs to the current interval.

## Output materialization

Sparse Event outputs choose an explicit boundary policy:

```text
HOLD   retain the latest present payload
ZERO   emit the payload on occurrences and zero/empty elsewhere
VALID  emit aligned payload plus <name>__valid
```

The physical Event backend aligns payload and valid phases before exposing the output port.

## State

`AccumulatorReg` and `FreezeReg` remain the foundational whole-vector state primitives. Periodic
Level state may infer a multicycle physical period. Event-clocked state is implemented for the
milestone's structural subset, including Event Freeze updates, compiler-owned `SumInto` state, and
direct unconditional Event accumulation without an intermediate bridge.

Broader Event update combinations, queues/backpressure, and richer burst contracts are intentionally
future work rather than implicit semantics.

## Runtime-open vectors

Whole Factorio signal maps remain runtime-open:

```python
missing = (required - stock).positive()
has_missing = missing.any()
request = missing.max()
gated = request.gate(enable)
```

Clock bridges preserve packed vectors rather than allocating state per signal lane.

## Validation

Canonical development checks are:

```fish
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

The flagship Clocked Flow acceptance test is
`tests/integration/test_multi_rate_event_ledger.py`: it compares irregular multi-rate semantic Event
simulation against the compiled physical circuit and checks shared bridge realization.

Before merging the Clocked Flow milestone into `main`, generate and test the focused in-game smoke
blueprint:

```fish
uv run python examples/clocked_flow_ingame_smoke.py
```

See [`docs/clocked-flow-merge-smoke.md`](docs/clocked-flow-merge-smoke.md) for the wiring schedule and
expected observations.
