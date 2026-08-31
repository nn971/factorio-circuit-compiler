from __future__ import annotations

import pytest

from factorio_circuit.simulate.compare import assert_same_periodic_stream
from tests.support.random_state_programs import (
    PeriodicStateProgram,
    StateCondition,
    StateOperation,
    build_periodic_state_circuit,
    generate_periodic_state_input_stream,
    generate_periodic_state_program,
    shrink_periodic_state_program,
)

_PROGRAM_SEEDS = (0x57A7E, 0xC10C, 0xB0A7D)


def _assert_periodic_state_program_equivalent(
    program: PeriodicStateProgram,
    *,
    optimize: bool,
    input_seed: int,
    cases: int = 12,
) -> int:
    result = build_periodic_state_circuit(program).compile(optimize=optimize)
    period = result.state_timing.uniform_period
    assert period is not None and period > 0
    stream = generate_periodic_state_input_stream(program, seed=input_seed, cases=cases)
    assert_same_periodic_stream(
        result.semantic_ir,
        result.physical_circuit,
        stream,
        period=period,
    )
    return period


@pytest.mark.parametrize("program_seed", _PROGRAM_SEEDS)
@pytest.mark.parametrize("optimize", [False, True])
def test_seeded_periodic_state_programs_match_physical_simulation(
    program_seed: int,
    optimize: bool,
) -> None:
    program = generate_periodic_state_program(program_seed)
    input_seed = program_seed ^ (0x5A7E if optimize else 0xC0DE)

    try:
        period = _assert_periodic_state_program_equivalent(
            program,
            optimize=optimize,
            input_seed=input_seed,
        )
    except AssertionError as original_error:

        def still_fails(candidate: PeriodicStateProgram) -> bool:
            try:
                _assert_periodic_state_program_equivalent(
                    candidate,
                    optimize=optimize,
                    input_seed=input_seed,
                )
            except AssertionError:
                return True
            return False

        minimized = shrink_periodic_state_program(program, still_fails)
        pytest.fail(
            "semantic/physical mismatch for generated periodic-state program\n"
            f"program_seed={program_seed}\n"
            f"input_seed={input_seed}\n"
            f"optimize={optimize}\n"
            "original program:\n"
            f"{program.describe()}\n"
            "minimized failing program:\n"
            f"{minimized.describe()}\n"
            f"original mismatch: {original_error}",
            pytrace=False,
        )

    # Every generated seed deliberately contains a state-dependent update so this layer exercises
    # a genuine multi-tick clock domain rather than merely re-testing period-1 vector feedback.
    assert period > 1


def test_periodic_state_program_shrinker_reduces_state_operations() -> None:
    program = PeriodicStateProgram(
        seed=11,
        vector_input_count=1,
        scalar_input_count=1,
        operations=(
            StateOperation(
                "add",
                scale=-2,
                condition=StateCondition("state", comparator="<", right=7),
            ),
            StateOperation(
                "add",
                scale=2,
                condition=StateCondition("scalar", comparator=">", right=0),
            ),
            StateOperation(
                "clear",
                condition=StateCondition("state", comparator="!=", right=0),
            ),
        ),
        outputs=("state", "count", "positive"),
    )

    def fails(candidate: PeriodicStateProgram) -> bool:
        return any(operation.kind == "clear" for operation in candidate.operations)

    minimized = shrink_periodic_state_program(program, fails)

    assert minimized.outputs == ("state",)
    assert minimized.operations == (StateOperation("clear"),)


def test_periodic_stream_comparator_rejects_invalid_period() -> None:
    program = PeriodicStateProgram(
        seed=12,
        vector_input_count=1,
        scalar_input_count=1,
        operations=(StateOperation("add"),),
        outputs=("state",),
    )
    result = build_periodic_state_circuit(program).compile(optimize=False)

    with pytest.raises(ValueError, match="period must be a positive integer"):
        assert_same_periodic_stream(result.semantic_ir, result.physical_circuit, [{}], period=0)
