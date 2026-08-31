from __future__ import annotations

import pytest

from factorio_circuit.simulate.compare import assert_equivalent_random
from tests.support.random_programs import (
    ScalarNode,
    ScalarProgram,
    ScalarRef,
    build_scalar_circuit,
    generate_scalar_program,
    shrink_scalar_program,
)

_PROGRAM_SEEDS = (0xA11CE, 0xC1AC017, 0xFACADE, 0x51A1A, 0xD1FF)


def _assert_program_equivalent(
    program: ScalarProgram,
    *,
    optimize: bool,
    input_seed: int,
    cases: int = 24,
) -> None:
    result = build_scalar_circuit(program).compile(optimize=optimize)
    assert_equivalent_random(
        result.semantic_ir,
        result.physical_circuit,
        cases=cases,
        seed=input_seed,
    )


@pytest.mark.parametrize("program_seed", _PROGRAM_SEEDS)
@pytest.mark.parametrize("optimize", [False, True])
def test_seeded_random_scalar_programs_match_physical_simulation(
    program_seed: int,
    optimize: bool,
) -> None:
    program = generate_scalar_program(program_seed)
    input_seed = program_seed ^ (0x5EED if optimize else 0xC0DE)

    try:
        _assert_program_equivalent(program, optimize=optimize, input_seed=input_seed)
    except AssertionError as original_error:

        def still_fails(candidate: ScalarProgram) -> bool:
            try:
                _assert_program_equivalent(
                    candidate,
                    optimize=optimize,
                    input_seed=input_seed,
                )
            except AssertionError:
                return True
            return False

        minimized = shrink_scalar_program(program, still_fails)
        pytest.fail(
            "semantic/physical mismatch for generated scalar program\n"
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


def test_scalar_program_shrinker_bypasses_irrelevant_expression_nodes() -> None:
    program = ScalarProgram(
        seed=7,
        input_count=2,
        nodes=(
            ScalarNode("+", (ScalarRef(0), 0)),
            ScalarNode("*", (ScalarRef(2), ScalarRef(1))),
            ScalarNode("|", (ScalarRef(3), 0)),
            ScalarNode("+", (ScalarRef(4), ScalarRef(0))),
        ),
        outputs=(ScalarRef(5), ScalarRef(2)),
    )

    def fails(candidate: ScalarProgram) -> bool:
        return any(node.op == "*" for node in candidate.nodes)

    minimized = shrink_scalar_program(program, fails)

    assert minimized.input_count == 2
    assert minimized.nodes == (ScalarNode("*", (ScalarRef(0), ScalarRef(1))),)
    assert minimized.outputs == (ScalarRef(2),)
