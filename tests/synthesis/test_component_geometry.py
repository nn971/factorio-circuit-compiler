from dataclasses import replace

import pytest

from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit
from factorio_circuit.synthesis.component_geometry import (
    ComponentAccessPoint,
    ComponentLayoutOptimizationProblem,
    ComponentRegion,
    RigidComponentConstraint,
    RigidComponentMember,
    lower_component_layout_problem,
    optimize_component_layout,
    validate_component_layout_problem,
)
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
)
from factorio_circuit.synthesis.placement import PlacementOptions


def _layout(positions: dict[int, tuple[float, float]]) -> Layout:
    circuit = PhysicalCircuit(
        "component_geometry",
        entities=[ConstantCombinator(object_id) for object_id in sorted(positions)],
    )
    return Layout(circuit, positions, (), (), (), ())


def _lattice(width: int = 12, height: int = 3) -> LegalPlacementLattice:
    unit = tuple((float(x), float(y)) for y in range(height) for x in range(width))
    wide = tuple((float(x) + 0.5, float(y)) for y in range(height) for x in range(width - 1))
    return LegalPlacementLattice(unit_sites=unit, wide_sites=wide)


def _problem(positions: dict[int, tuple[float, float]]) -> LayoutOptimizationProblem:
    return LayoutOptimizationProblem(_layout(positions), _lattice(), safe_wire_span=3.0)


def test_lowering_freezes_members_and_reserves_component_geometry() -> None:
    base = _problem({1: (1.0, 1.0), 2: (8.0, 1.0)})
    component = RigidComponentConstraint(
        "cell",
        origin=(1.0, 1.0),
        members=(RigidComponentMember(1, (0.0, 0.0)),),
        footprints=(ComponentRegion(-0.5, -0.5, 0.5, 0.5),),
        keepouts=(ComponentRegion(0.5, -0.5, 1.5, 0.5),),
        adapter_regions=(ComponentRegion(1.5, -0.5, 2.5, 0.5),),
        access_points=(ComponentAccessPoint("east", (0.5, 0.0)),),
    )

    lowered = lower_component_layout_problem(ComponentLayoutOptimizationProblem(base, (component,)))

    assert lowered.fixed_positions == {1: (1.0, 1.0)}
    assert (1.0, 1.0) not in lowered.lattice.unit_sites
    assert (2.0, 1.0) not in lowered.lattice.unit_sites
    assert (3.0, 1.0) not in lowered.lattice.unit_sites
    assert (4.0, 1.0) in lowered.lattice.unit_sites
    assert component.access_positions() == {"east": (1.5, 1.0)}


def test_external_object_cannot_occupy_component_owned_geometry() -> None:
    base = _problem({1: (0.0, 1.0), 2: (2.0, 1.0)})
    component = RigidComponentConstraint(
        "owned",
        origin=(0.0, 1.0),
        members=(RigidComponentMember(1, (0.0, 0.0)),),
        footprints=(ComponentRegion(-0.5, -0.5, 2.5, 0.5),),
    )

    with pytest.raises(ValueError, match="owned/keepout geometry"):
        lower_component_layout_problem(ComponentLayoutOptimizationProblem(base, (component,)))


def test_member_must_fit_completely_inside_component_footprint() -> None:
    base = _problem({1: (1.0, 1.0)})
    component = RigidComponentConstraint(
        "too-tight",
        origin=(1.0, 1.0),
        members=(RigidComponentMember(1, (0.0, 0.0)),),
        footprints=(ComponentRegion(-0.25, -0.25, 0.75, 0.75),),
    )

    with pytest.raises(ValueError, match="does not fit completely"):
        lower_component_layout_problem(ComponentLayoutOptimizationProblem(base, (component,)))


def test_reserved_adapter_region_must_be_empty_even_of_component_members() -> None:
    base = _problem({1: (1.0, 1.0)})
    component = RigidComponentConstraint(
        "adapter",
        origin=(1.0, 1.0),
        members=(RigidComponentMember(1, (0.0, 0.0)),),
        footprints=(ComponentRegion(-0.5, -0.5, 0.5, 0.5),),
        adapter_regions=(ComponentRegion(-0.25, -0.25, 0.25, 0.25),),
    )

    with pytest.raises(ValueError, match="reserved adapter region"):
        lower_component_layout_problem(ComponentLayoutOptimizationProblem(base, (component,)))


def test_access_points_must_lie_on_a_declared_footprint_boundary() -> None:
    with pytest.raises(ValueError, match="not on a footprint boundary"):
        RigidComponentConstraint(
            "bad-access",
            origin=(0.0, 0.0),
            members=(RigidComponentMember(1, (0.0, 0.0)),),
            footprints=(ComponentRegion(-1.0, -1.0, 1.0, 1.0),),
            access_points=(ComponentAccessPoint("floating", (0.0, 0.0)),),
        )


def test_quarter_turn_pose_applies_to_members_regions_and_access_points() -> None:
    base = _problem({1: (4.0, 6.0)})
    component = RigidComponentConstraint(
        "rotated",
        origin=(4.0, 5.0),
        quarter_turns=1,
        allowed_quarter_turns=(0, 1, 2, 3),
        members=(RigidComponentMember(1, (1.0, 0.0)),),
        footprints=(ComponentRegion(0.5, -0.5, 1.5, 0.5),),
        access_points=(ComponentAccessPoint("tip", (1.5, 0.0)),),
    )

    lowered = lower_component_layout_problem(ComponentLayoutOptimizationProblem(base, (component,)))

    assert lowered.fixed_positions[1] == (4.0, 6.0)
    assert component.access_positions() == {"tip": (4.0, 6.5)}
    assert component.absolute_footprints() == (ComponentRegion(3.5, 5.5, 4.5, 6.5),)


def test_one_physical_object_cannot_belong_to_two_rigid_components() -> None:
    base = _problem({1: (1.0, 1.0)})
    member = (RigidComponentMember(1, (0.0, 0.0)),)
    footprint = (ComponentRegion(-0.5, -0.5, 0.5, 0.5),)
    left = RigidComponentConstraint("left", (1.0, 1.0), member, footprint)
    right = RigidComponentConstraint("right", (1.0, 1.0), member, footprint)

    with pytest.raises(ValueError, match="belongs to both"):
        lower_component_layout_problem(ComponentLayoutOptimizationProblem(base, (left, right)))


def test_component_optimization_preserves_rigid_member_and_exclusion_region() -> None:
    base = _problem({1: (2.0, 1.0), 2: (10.0, 1.0)})
    component = RigidComponentConstraint(
        "fixed-cell",
        origin=(2.0, 1.0),
        members=(RigidComponentMember(1, (0.0, 0.0)),),
        footprints=(ComponentRegion(-0.5, -0.5, 0.5, 0.5),),
        keepouts=(ComponentRegion(0.5, -0.5, 2.5, 0.5),),
    )
    problem = ComponentLayoutOptimizationProblem(base, (component,))

    result = optimize_component_layout(
        problem,
        options=PlacementOptions(anchor_io=False, iterations=512, random_seed=7),
    )

    assert result.layout.positions[1] == (2.0, 1.0)
    external = result.layout.positions[2]
    exclusion = (*component.absolute_footprints(), *component.absolute_keepouts())
    assert not any(region.overlaps_box(external, (0.5, 0.5)) for region in exclusion)
    validate_component_layout_problem(
        replace(problem, layout_problem=replace(base, layout=result.layout))
    )


def test_current_pose_must_be_one_of_the_declared_future_legal_poses() -> None:
    with pytest.raises(ValueError, match="orientation is not allowed"):
        RigidComponentConstraint(
            "orientation",
            origin=(0.0, 0.0),
            quarter_turns=1,
            members=(RigidComponentMember(1, (0.0, 0.0)),),
            footprints=(ComponentRegion(-0.5, -0.5, 0.5, 0.5),),
        )

    with pytest.raises(ValueError, match="origin is not allowed"):
        RigidComponentConstraint(
            "translation",
            origin=(1.0, 1.0),
            allowed_origins=((0.0, 0.0),),
            members=(RigidComponentMember(1, (0.0, 0.0)),),
            footprints=(ComponentRegion(-0.5, -0.5, 0.5, 0.5),),
        )
