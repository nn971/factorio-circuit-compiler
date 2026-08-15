import pytest

from factorio_circuit.analysis import StateTimingError, analyze_clocked_timing
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Clock,
    ClockProvenance,
    Constant,
    ReturnValue,
    VectorBinaryOp,
    VectorConstant,
)
from factorio_circuit.ir.state import FreezeRegister, StateTransition, VectorRegisterRead


def _one_register_recurrence(clock: Clock) -> CircuitModule:
    """Build a recurrence whose physical implementation needs period at least two."""

    register = FreezeRegister("memory")
    old = VectorRegisterRead(register, offset=0, order=0, name="memory")
    delayed_value = VectorBinaryOp("+", old, VectorConstant(()))
    return CircuitModule(
        name="clock_contract_recurrence",
        inputs=(),
        operations=(),
        output=ReturnValue((old,)),
        state_registers=(register,),
        transitions=(
            StateTransition(
                register=register,
                kind="set",
                clock=clock,
                order=1,
                value=delayed_value,
                when=Constant(1),
            ),
        ),
    )


def test_inferred_clock_enlarges_to_the_minimum_feasible_period() -> None:
    clock = Clock("inferred", ClockProvenance.INFERRED, guaranteed_min_separation=1)

    plan = analyze_clocked_timing(_one_register_recurrence(clock))

    assert plan.uniform_period == 2
    assert plan.domains[0].period == 2
    assert plan.domains[0].clock_id == clock.clock_id


@pytest.mark.parametrize("period", [2, 3])
def test_fixed_periodic_clock_keeps_its_declared_period(period: int) -> None:
    clock = Clock("fixed", ClockProvenance.FIXED_PERIODIC, guaranteed_min_separation=period)

    plan = analyze_clocked_timing(_one_register_recurrence(clock))

    assert plan.uniform_period == period
    assert plan.domains[0].period == period
    assert plan.domains[0].clock_id == clock.clock_id


def test_fixed_periodic_clock_rejects_an_infeasible_declared_period() -> None:
    clock = Clock("fixed-too-fast", ClockProvenance.FIXED_PERIODIC, guaranteed_min_separation=1)

    with pytest.raises(
        StateTimingError,
        match="fixed periodic clock.*period 1.*requires at least 2",
    ):
        analyze_clocked_timing(_one_register_recurrence(clock))
