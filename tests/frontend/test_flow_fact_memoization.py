from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from factorio_circuit import Circuit, SignalId
from factorio_circuit.analysis.state_timing import analyze_normalized_state_timing
from factorio_circuit.ir.semantic import Clock, ClockProvenance
from factorio_circuit.ir.state import FreezeRegister, FreezeSet, StateTransition, state_transitions
from factorio_circuit.lowering.frontend_to_ir import normalize_module


class _HashBomb:
    def __hash__(self) -> int:
        raise AssertionError("deep expression values must not be structurally hashed")


def _shared_scalar_dag(name: str) -> Circuit:
    circuit = Circuit(name)
    left = circuit.input("left")
    right = circuit.input("right")

    value = left + right
    for _ in range(12):
        condition = value != 0
        value = condition.select(value + 1, value - 1)

    circuit.output("value", value)
    return circuit


def test_shared_scalar_dag_elaborates_without_tree_reexpansion() -> None:
    module = _shared_scalar_dag("shared_scalar_flow_facts").build()

    assert module.output.names == ("value",)


def test_shared_scalar_dag_normalizes_without_root_clock_tree_reexpansion() -> None:
    module = normalize_module(_shared_scalar_dag("shared_scalar_root_clock").build())

    assert module.output.names == ("value",)
    assert module.output.values[0].flow is not None  # type: ignore[attr-defined]


def test_shared_state_dag_timing_does_not_expand_occurrence_tree() -> None:
    circuit = Circuit("shared_state_timing")
    state = circuit.freeze("state")
    old_state = state.sample()
    value = old_state.signal(SignalId("item", "iron-plate")) + 1
    for _ in range(10):
        condition = value != 0
        value = condition.select(value + 1, value - 1)

    state.set(old_state, when=value)
    circuit.output("value", value)

    normalized = normalize_module(circuit.build())
    plan = analyze_normalized_state_timing(normalized)

    assert plan.uniform_period is not None
    assert len(plan.for_register(normalized.state_registers[0]).reads) == 1


def test_projected_state_transition_uses_legacy_provenance_before_structural_hashing() -> None:
    register = FreezeRegister("state")
    clock = Clock("state-clock", ClockProvenance.INFERRED)
    value = cast(Any, _HashBomb())
    when = cast(Any, _HashBomb())
    operation = FreezeSet(register, value, when)
    transition = StateTransition(
        register=register,
        kind="set",
        clock=clock,
        value=value,
        when=when,
        legacy=operation,
    )
    module = SimpleNamespace(
        name="state_projection",
        transitions=(transition,),
        state_operations=(operation,),
        event_state_operations=(),
        register_clocks=((register, clock),),
    )

    assert state_transitions(module) == (transition,)
