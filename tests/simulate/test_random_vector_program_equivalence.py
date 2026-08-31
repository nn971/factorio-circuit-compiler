from __future__ import annotations

import pytest

from factorio_circuit.simulate.compare import assert_same_stream
from tests.support.random_vector_programs import (
    ScalarInputRef,
    VectorNode,
    VectorProgram,
    VectorRef,
    build_vector_circuit,
    generate_vector_input_stream,
    generate_vector_program,
    shrink_vector_program,
)

_PROGRAM_SEEDS = (0xBADC0DE, 0x51C6A1, 0x2EC70)


def _assert_vector_program_equivalent(
    program: VectorProgram,
    *,
    optimize: bool,
    input_seed: int,
    cases: int = 18,
) -> None:
    result = build_vector_circuit(program).compile(optimize=optimize)
    stream = generate_vector_input_stream(program, seed=input_seed, cases=cases)
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)


@pytest.mark.parametrize("program_seed", _PROGRAM_SEEDS)
@pytest.mark.parametrize("optimize", [False, True])
def test_seeded_random_vector_programs_match_physical_simulation(
    program_seed: int,
    optimize: bool,
) -> None:
    program = generate_vector_program(program_seed)
    input_seed = program_seed ^ (0xA11CE if optimize else 0xC0DE)

    try:
        _assert_vector_program_equivalent(
            program,
            optimize=optimize,
            input_seed=input_seed,
        )
    except AssertionError as original_error:

        def still_fails(candidate: VectorProgram) -> bool:
            try:
                _assert_vector_program_equivalent(
                    candidate,
                    optimize=optimize,
                    input_seed=input_seed,
                )
            except AssertionError:
                return True
            return False

        minimized = shrink_vector_program(program, still_fails)
        pytest.fail(
            "semantic/physical mismatch for generated vector program\n"
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


def test_vector_program_shrinker_bypasses_irrelevant_vector_nodes() -> None:
    program = VectorProgram(
        seed=9,
        vector_input_count=2,
        scalar_input_count=1,
        nodes=(
            VectorNode("filter_gt", (VectorRef(0), 0)),
            VectorNode("mul", (VectorRef(2), ScalarInputRef(0))),
            VectorNode("neg", (VectorRef(3),)),
            VectorNode("add", (VectorRef(4), VectorRef(1))),
        ),
        outputs=(VectorRef(5), VectorRef(2)),
    )

    def fails(candidate: VectorProgram) -> bool:
        return any(node.op == "mul" for node in candidate.nodes)

    minimized = shrink_vector_program(program, fails)

    assert minimized.vector_input_count == 2
    assert minimized.scalar_input_count == 1
    assert minimized.nodes == (VectorNode("mul", (VectorRef(0), ScalarInputRef(0))),)
    assert minimized.outputs == (VectorRef(2),)
