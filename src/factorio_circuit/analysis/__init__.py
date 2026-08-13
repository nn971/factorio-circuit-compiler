"""Compiler analyses."""

from factorio_circuit.ir.semantic import CircuitModule, VectorSignal
from factorio_circuit.ir.state import AccumulatorAdd, FreezeSet

from .state_timing import (
    RegisterTiming,
    StateReadTiming,
    StateTimingError,
    StateTimingPlan,
    analyze_state_timing as _analyze_state_timing,
    earliest_scalar_phase,
    earliest_vector_phase,
)


def _contains_new_vector_logic(value: object) -> bool:
    from factorio_circuit.frontend import _VectorBinaryOp, _VectorFilter, _VectorScalarOp

    if isinstance(value, _VectorBinaryOp):
        return True
    if isinstance(value, _VectorScalarOp):
        return True
    if isinstance(value, _VectorFilter):
        return True
    if isinstance(value, VectorSignal):
        return _contains_new_vector_logic(value.vector)
    return False


def analyze_state_timing(module: CircuitModule) -> StateTimingPlan:
    """Use the established state solver, with a stateless fast path for vector algebra."""

    if not module.state_registers:
        return StateTimingPlan(())
    if any(_contains_new_vector_logic(value) for value in module.output.values):
        raise StateTimingError(
            "runtime-open vector expressions on stateful circuits belong to the next state-timing "
            "milestone"
        )
    for operation in module.state_operations:
        if isinstance(operation, (AccumulatorAdd, FreezeSet)) and _contains_new_vector_logic(
            operation.value
        ):
            raise StateTimingError(
                "runtime-open vector expressions feeding state belong to the next state-timing "
                "milestone"
            )
    return _analyze_state_timing(module)


__all__ = [
    "RegisterTiming",
    "StateReadTiming",
    "StateTimingError",
    "StateTimingPlan",
    "analyze_state_timing",
    "earliest_scalar_phase",
    "earliest_vector_phase",
]
