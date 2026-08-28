from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.multilevel import (
    CoarseningLevel,
    PlacementMacro,
    build_multilevel_hierarchy,
)
from factorio_circuit.synthesis.multilevel_uncoarsen import (
    HierarchicalUncoarsenOptions,
    expand_macro_level,
    hierarchical_uncoarsen,
)
from factorio_circuit.synthesis.multilevel_zoom import (
    MacroGeometry,
    build_macro_geometry,
    compact_macro_geometry,
    validate_macro_placement,
)


def _red_connection(left: int, right: int) -> WireConnection:
    return WireConnection(
        WireEndpoint(left, Connector.SINGLE),
        WireEndpoint(right, Connector.SINGLE),
        WireColor.RED,
    )


def test_pair_expansion_splits_siblings_around_parent_center() -> None:
    circuit = PhysicalCircuit(
        "pair-split",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
    )
    coarse_level = CoarseningLevel((PlacementMacro((1, 2)),))
    finer_level = CoarseningLevel((PlacementMacro((1,)), PlacementMacro((2,))))
    coarse = MacroGeometry(
        coarse_level,
        ((10.0, 10.0),),
        ((2.0, 1.0),),
        2.0,
    )

    expanded = expand_macro_level(
        circuit,
        {1: (0.0, 0.0), 2: (20.0, 0.0)},
        coarse,
        finer_level,
        target_density=1.0,
        max_legalization_radius=0,
    )

    validate_macro_placement(expanded)
    assert expanded.centers == ((9.5, 10.0), (10.5, 10.0))
    assert expanded.half_extents == ((0.5, 0.5), (0.5, 0.5))


def test_uncoarsening_removes_rounding_slack_at_singleton_level() -> None:
    circuit = PhysicalCircuit(
        "density-schedule",
        entities=[ConstantCombinator(entity_id) for entity_id in range(1, 5)],
        connections=[_red_connection(entity_id, entity_id + 1) for entity_id in range(1, 4)],
    )
    seed = {entity_id: (float(4 * entity_id), 0.0) for entity_id in range(1, 5)}
    hierarchy = build_multilevel_hierarchy(circuit, target_macros=1)
    source = build_macro_geometry(
        circuit,
        seed,
        hierarchy.levels[-1],
        target_density=0.8,
    )
    zoom = compact_macro_geometry(source, hierarchy.hyperedges)
    assert zoom.accepted_scale is not None

    result = hierarchical_uncoarsen(
        circuit,
        seed,
        hierarchy,
        zoom.geometry,
        options=HierarchicalUncoarsenOptions(
            target_density=0.8,
            finest_density=1.0,
            proposals_per_level=0,
        ),
    )

    validate_macro_placement(result.geometry)
    assert result.levels[-1].target_density == 1.0
    assert result.geometry.half_extents == ((0.5, 0.5),) * 4
