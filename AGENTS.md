# AGENTS.md

## Scope and architecture

This project compiles symbolic Python circuits into Factorio circuit-network blueprints. The
canonical Level/physical `compile_circuit` pipeline lowers the frontend, optionally optimizes semantic IR,
analyzes state timing, lowers to abstract physical IR, synthesizes a `Layout`, and serializes the
layout to blueprint output. `compile_abstract_circuit` is its compatibility alias.

Ownership is strict: semantic IR represents logical streams and explicit state transitions;
abstract physical IR represents exact combinators, abstract nets/signals, compatibility metadata,
and phases; physical synthesis allocates concrete signals and wires and owns placement and the
final `Layout`; blueprint generation only serializes that layout.

Event-bearing circuits still undergo frontend elaboration and semantic `CircuitModule` construction.
They are a separate semantic/reference lane: use `simulate_events()` and its reference-only
Event/SampleOn materialization results. Level/physical routes raise `EventCompilationError` before
`StateTimingPlan` or semantic-to-physical lowering; no abstract physical IR, synthesis, blueprint
generation, or Level simulation follows. No physical Event pulse, storage, bridge, output policy, or
valid-wiring support is implemented.

## Frontend and timing invariants

- Logical steps and Factorio game ticks are distinct. `source.sample()` reads the current logical
  step, `circuit.step(n)` advances logical time, and `Circuit.tick()` is not a logical-time alias.
  `register.value` is a deprecated compatibility alias only; use `register.sample()` for new code.
- Stateless combinators preserve logical step but add physical latency. Each connected state
  clock domain has an inferred integer period; feed-forward latency does not raise it, while
  recurrence constraints can. Ordinary state dependencies share a domain; independent state
  components may differ. Cross-domain state communication requires explicit rate-crossing
  semantics, and zero-logical-distance positive-latency cycles are illegal.
- Reads and updates retain elaboration order; a read cannot split one compound state transition.
  For period `P > 1`, lowering must gate state writes so intermediate game ticks hold state.
- Runtime-open vector nets and fixed target signals remain explicit. `Each` is the whole-vector
  mechanism, and both red and green networks are usable. Respect combinator one-tick latency and
  wire reach; never emit an invalid long wire.

## Setup and validation

Use Python `>=3.12` and `uv`:

```bash
python -m pip install uv
uv sync --extra dev
```

CI validation commands, in order:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

For focused pytest runs, select a file or node, for example:

```bash
uv run pytest tests/timing/test_state_timing.py
uv run pytest tests/timing/test_state_timing.py::test_elastic_transition_is_pinned_by_bracketing_reads
uv run pytest tests/integration/test_events.py
```

For applicable Level-module changes, validate logical/semantic behavior and physical tick-level
simulation or structural timing. Test generated blueprints in Factorio only for representative
Level/stateful circuits; Event modules are validated through the semantic Event integration test and
have no physical Factorio support.
