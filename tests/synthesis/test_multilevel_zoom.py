from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis.multilevel import (
    CoarseningLevel,
    ImplementationHyperedge,
    PlacementMacro,
)
from factorio_circuit.synthesis.multilevel_zoom import (
    build_macro_geometry,
    compact_macro_geometry,
    macro_placement_metrics,
    try_macro_zoom,
    validate_macro_placement,
)


def _constant_circuit(count: int) -> PhysicalCircuit:
    return PhysicalCircuit(
        "macro-zoom-test",
        entities=[ConstantCombinator(entity_id) for entity_id in range(1, count + 1)],
    )


def test_macro_footprint_depends_on_entity_area_not_seed_spread() -> None:
    circuit = _constant_circuit(4)
    level = CoarseningLevel((PlacementMacro((1, 2, 3, 4)),))
    compact_positions = {1: (0.0, 0.0), 2: (1.0, 0.0), 3: (0.0, 1.0), 4: (1.0, 1.0)}
    sparse_positions = {1: (0.0, 0.0), 2: (100.0, 0.0), 3: (0.0, 100.0), 4: (100.0, 100.0)}

    compact = build_macro_geometry(circuit, compact_positions, level, target_density=0.8)
    sparse = build_macro_geometry(circuit, sparse_positions, level, target_density=0.8)

    assert compact.half_extents == sparse.half_extents == ((1.5, 1.0),)
    assert compact.implementation_area == sparse.implementation_area == 4.0
    assert compact.centers != sparse.centers


def test_global_macro_zoom_contracts_sparse_centers() -> None:
    circuit = _constant_circuit(4)
    level = CoarseningLevel(tuple(PlacementMacro((entity_id,)) for entity_id in range(1, 5)))
    positions = {1: (0.0, 0.0), 2: (10.0, 0.0), 3: (0.0, 10.0), 4: (10.0, 10.0)}
    geometry = build_macro_geometry(circuit, positions, level)
    hyperedges = (ImplementationHyperedge((1, 2, 3, 4), WireColor.RED),)
    before = macro_placement_metrics(geometry, hyperedges)

    result = compact_macro_geometry(geometry, hyperedges, scales=(0.25,))

    validate_macro_placement(result.geometry)
    assert result.accepted_scale == 0.25
    assert result.after.bounding_area < before.bounding_area
    assert result.after.implementation_occupancy > before.implementation_occupancy
    assert result.after.hypernet_hpwl < before.hypernet_hpwl


def test_fixed_singleton_macro_stays_exact_during_zoom() -> None:
    circuit = _constant_circuit(3)
    level = CoarseningLevel(
        (
            PlacementMacro((1,), fixed=True),
            PlacementMacro((2,)),
            PlacementMacro((3,)),
        )
    )
    positions = {1: (0.0, 0.0), 2: (12.0, 0.0), 3: (0.0, 12.0)}
    geometry = build_macro_geometry(circuit, positions, level)

    candidate, failure = try_macro_zoom(geometry, scale=0.5)

    assert failure is None
    assert candidate is not None
    assert candidate.centers[0] == positions[1]
    validate_macro_placement(candidate)


def test_macro_zoom_is_deterministic() -> None:
    circuit = _constant_circuit(6)
    level = CoarseningLevel(
        (
            PlacementMacro((1, 2)),
            PlacementMacro((3, 4)),
            PlacementMacro((5, 6)),
        )
    )
    positions = {
        1: (0.0, 0.0),
        2: (2.0, 0.0),
        3: (20.0, 0.0),
        4: (22.0, 0.0),
        5: (10.0, 20.0),
        6: (12.0, 20.0),
    }
    geometry = build_macro_geometry(circuit, positions, level)

    left, left_failure = try_macro_zoom(geometry, scale=0.35)
    right, right_failure = try_macro_zoom(geometry, scale=0.35)

    assert left_failure == right_failure
    assert left == right
