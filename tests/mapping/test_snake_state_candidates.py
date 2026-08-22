from benchmarks.snake.model import build_snake_circuit
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    build_periodic_state_mapping_problem,
    ordinary_state_candidates,
)
from factorio_circuit.sampling import SamplingPolicy


def test_default_snake_has_complete_ordinary_state_cell_coverage() -> None:
    module = lower_frontend(build_snake_circuit(render_framebuffer=False))
    problem = build_periodic_state_mapping_problem(
        module,
        period=60,
        output_phases=(119,) * len(module.output.values),
        sampling_policy=SamplingPolicy.ALAP,
    )

    candidates = ordinary_state_candidates(problem)

    assert len(candidates) == 9
    assert len({item.id for item in candidates}) == 9
    assert {item.register_name for item in candidates} == {
        "head_x",
        "head_y",
        "direction",
        "queued_direction",
        "score",
        "dead",
        "started",
        "body_ttl",
        "body_mask",
    }
    costs = {item.register_name: item.entity_cost for item in candidates}
    assert sum(costs.values()) == 36
    for register_name in {
        "head_x",
        "head_y",
        "score",
        "direction",
        "queued_direction",
        "dead",
        "started",
        "body_ttl",
        "body_mask",
    }:
        assert costs[register_name] == 4
        candidate = next(item for item in candidates if item.register_name == register_name)
        assert candidate.commit_phase_offset == -2
