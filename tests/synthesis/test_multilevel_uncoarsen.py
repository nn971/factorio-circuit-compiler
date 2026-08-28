from dataclasses import replace

import pytest

from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.layout import Layout, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
    validate_physical_layout,
)
from factorio_circuit.synthesis.multilevel import (
    CoarseningLevel,
    PlacementMacro,
    build_multilevel_hierarchy,
)
from factorio_circuit.synthesis.multilevel_uncoarsen import (
    HierarchicalUncoarsenOptions,
    child_parent_indices,
    hierarchical_uncoarsen,
    legalize_singleton_implementation_targets,
)
from factorio_circuit.synthesis.multilevel_zoom import (
    MacroGeometry,
    build_macro_geometry,
    compact_macro_geometry,
    validate_macro_placement,
)
from factorio_circuit.synthesis.transactional_reroute import (
    reroute_implementation_transactionally,
)


def _red_connection(left: int, right: int) -> WireConnection:
    return WireConnection(
        WireEndpoint(left, Connector.SINGLE),
        WireEndpoint(right, Connector.SINGLE),
        WireColor.RED,
    )


def _chain_circuit(count: int) -> PhysicalCircuit:
    return PhysicalCircuit(
        "uncoarsen-chain",
        entities=[ConstantCombinator(entity_id) for entity_id in range(1, count + 1)],
        connections=[_red_connection(entity_id, entity_id + 1) for entity_id in range(1, count)],
    )


def test_child_parent_mapping_follows_set_containment() -> None:
    coarse = CoarseningLevel(
        (
            PlacementMacro((1, 2, 3, 4)),
            PlacementMacro((5, 6, 7, 8)),
        )
    )
    finer = CoarseningLevel(
        (
            PlacementMacro((1, 2)),
            PlacementMacro((3, 4)),
            PlacementMacro((5, 6)),
            PlacementMacro((7, 8)),
        )
    )

    assert child_parent_indices(coarse, finer) == (0, 0, 1, 1)


def test_child_parent_mapping_rejects_cross_parent_macro() -> None:
    coarse = CoarseningLevel((PlacementMacro((1, 2)), PlacementMacro((3, 4))))
    finer = CoarseningLevel((PlacementMacro((1, 3)), PlacementMacro((2,)), PlacementMacro((4,))))

    with pytest.raises(ValueError, match="crosses coarse parents"):
        child_parent_indices(coarse, finer)


def test_hierarchical_uncoarsen_reaches_singletons_and_preserves_fixed_macro() -> None:
    circuit = _chain_circuit(8)
    seed_positions = {
        entity_id: (float(4 * (entity_id - 1)), 0.0) for entity_id in range(1, 9)
    }
    hierarchy = build_multilevel_hierarchy(
        circuit,
        fixed_entities=frozenset({1}),
        target_macros=3,
    )
    source = build_macro_geometry(circuit, seed_positions, hierarchy.levels[-1])
    zoom = compact_macro_geometry(source, hierarchy.hyperedges)
    assert zoom.accepted_scale is not None

    result = hierarchical_uncoarsen(
        circuit,
        seed_positions,
        hierarchy,
        zoom.geometry,
        options=HierarchicalUncoarsenOptions(
            proposals_per_level=128,
            random_seed=4,
            local_search_radius=1,
        ),
    )

    validate_macro_placement(result.geometry)
    assert result.geometry.level == hierarchy.levels[0]
    assert all(len(macro.members) == 1 for macro in result.geometry.level.macros)
    fixed_index = next(
        index for index, macro in enumerate(result.geometry.level.macros) if macro.members == (1,)
    )
    assert result.geometry.centers[fixed_index] == seed_positions[1]
    assert [row.macro_count for row in result.levels] == [
        len(level.macros) for level in reversed(hierarchy.levels[:-1])
    ]


def test_singleton_projection_uses_real_legal_sites_and_fixed_positions() -> None:
    circuit = PhysicalCircuit(
        "singleton-projection",
        entities=[ConstantCombinator(1), ConstantCombinator(2), ConstantCombinator(3)],
    )
    layout = Layout(circuit, {1: (0.0, 0.0), 2: (2.0, 0.0), 3: (4.0, 0.0)}, (), (), (), ())
    lattice = LegalPlacementLattice(
        unit_sites=tuple((float(x), float(y)) for y in range(4) for x in range(8)),
        wide_sites=(),
    )
    problem = LayoutOptimizationProblem(
        layout,
        lattice,
        safe_wire_span=3.0,
        fixed_positions={1: (0.0, 0.0)},
    )
    level = CoarseningLevel(
        (
            PlacementMacro((1,), fixed=True),
            PlacementMacro((2,)),
            PlacementMacro((3,)),
        )
    )
    geometry = MacroGeometry(
        level,
        ((0.0, 0.0), (2.4, 1.2), (2.6, 1.2)),
        ((0.5, 0.5),) * 3,
        3.0,
    )

    positions = legalize_singleton_implementation_targets(problem, geometry, search_radius=4)

    assert positions[1] == (0.0, 0.0)
    assert set(positions.values()) <= set(lattice.unit_sites)
    assert len(set(positions.values())) == 3


def test_singleton_projection_hands_complete_candidate_to_transactional_rerouter() -> None:
    circuit = PhysicalCircuit(
        "uncoarsen-reroute-handoff",
        entities=[ConstantCombinator(1), ConstantCombinator(2)],
        connections=[_red_connection(1, 2)],
    )
    layout = Layout(
        circuit,
        {1: (0.0, 0.0), 2: (2.0, 0.0)},
        (),
        (LayoutWire(1, 1, 2, 1, WireColor.RED),),
        (),
        (),
    )
    lattice = LegalPlacementLattice(
        unit_sites=tuple((float(x), 0.0) for x in range(12)),
        wide_sites=(),
    )
    problem = LayoutOptimizationProblem(layout, lattice, safe_wire_span=2.1)
    level = CoarseningLevel((PlacementMacro((1,)), PlacementMacro((2,))))
    geometry = MacroGeometry(level, ((0.0, 0.0), (7.0, 0.0)), ((0.5, 0.5),) * 2, 2.0)

    positions = legalize_singleton_implementation_targets(problem, geometry, search_radius=2)
    rerouted = reroute_implementation_transactionally(problem, positions)

    assert rerouted.succeeded
    assert rerouted.after.relay_count >= 1
    validate_physical_layout(replace(problem, layout=rerouted.layout))
