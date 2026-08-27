from benchmarks.layout_optimizer_corpus import _rectangular_lattice
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit
from factorio_circuit.synthesis.global_zoom import compact_by_global_zoom, try_global_zoom
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    physical_layout_metrics,
    validate_physical_layout,
)


def _sparse_independent_problem() -> LayoutOptimizationProblem:
    entities = [ConstantCombinator(entity_id) for entity_id in range(1, 17)]
    positions = {
        entity.id: (
            float(((entity.id - 1) % 4) * 4),
            float(((entity.id - 1) // 4) * 4),
        )
        for entity in entities
    }
    layout = Layout(PhysicalCircuit("zoom-sparse", entities=entities), positions, (), (), (), ())
    return LayoutOptimizationProblem(layout, _rectangular_lattice(16, 16), safe_wire_span=7.0)


def test_global_zoom_compacts_sparse_shape_without_shape_specific_moves() -> None:
    problem = _sparse_independent_problem()
    before = physical_layout_metrics(problem.layout)

    result = compact_by_global_zoom(problem, scales=(0.5,), max_passes=1)

    validate_physical_layout(LayoutOptimizationProblem(result.layout, problem.lattice, 7.0))
    assert result.accepted_scales == (0.5,)
    assert result.after.relay_count == 0
    assert result.after.occupied_area < before.occupied_area


def test_global_zoom_preserves_fixed_positions_exactly() -> None:
    base = _sparse_independent_problem()
    fixed = {1: base.layout.positions[1], 16: base.layout.positions[16]}
    problem = LayoutOptimizationProblem(
        base.layout,
        base.lattice,
        safe_wire_span=base.safe_wire_span,
        fixed_positions=fixed,
    )

    candidate, failure = try_global_zoom(problem, scale=0.9)

    assert failure is None
    assert candidate is not None
    assert candidate.positions[1] == fixed[1]
    assert candidate.positions[16] == fixed[16]
    validate_physical_layout(
        LayoutOptimizationProblem(
            candidate,
            problem.lattice,
            safe_wire_span=problem.safe_wire_span,
            fixed_positions=fixed,
        )
    )
