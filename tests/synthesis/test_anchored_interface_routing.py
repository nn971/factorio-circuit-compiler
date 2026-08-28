from dataclasses import replace

from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    InputPort,
    OutputPort,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.anchored_interface_routing import (
    AnchoredInterfaceLayoutProblem,
    PublicPortAnchorConstraint,
    route_anchored_interfaces_transactionally,
    validate_anchored_interface_routing,
)
from factorio_circuit.synthesis.component_geometry import (
    ComponentAccessPoint,
    ComponentLayoutOptimizationProblem,
    ComponentRegion,
    RigidComponentConstraint,
    RigidComponentMember,
    optimize_component_layout,
    validate_component_layout_problem,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
)
from factorio_circuit.synthesis.placement import PlacementOptions


def _connection(left: int, right: int) -> WireConnection:
    return WireConnection(
        WireEndpoint(left, Connector.SINGLE),
        WireEndpoint(right, Connector.SINGLE),
        WireColor.RED,
    )


def _lattice() -> LegalPlacementLattice:
    sites = tuple((float(x), float(y)) for y in range(-2, 3) for x in range(-2, 9))
    return LegalPlacementLattice(unit_sites=sites, wide_sites=())


def _component_problem(
    *,
    output: bool = False,
    blocker_at_anchor: bool = False,
    port_enters_component: bool = True,
    marker_fixed_at: tuple[float, float] | None = None,
) -> ComponentLayoutOptimizationProblem:
    entities = [
        ConstantCombinator(1, annotation_only=True),
        ConstantCombinator(2),
    ]
    connections: list[WireConnection] = []
    inputs: list[InputPort] = []
    outputs: list[OutputPort] = []
    if output:
        outputs.append(OutputPort("public", 1, None, 0))
    else:
        inputs.append(InputPort("public", 1, None))

    positions: dict[int, tuple[float, float]] = {1: (0.0, 0.0), 2: (4.0, 0.0)}
    relays: tuple[LayoutRelay, ...] = ()
    wires: tuple[LayoutWire, ...] = ()
    if port_enters_component:
        connections.append(_connection(1, 2))
        relays = (LayoutRelay(99, (2.0, 0.0), "old scaffold"),)
        positions[99] = (2.0, 0.0)
        wires = (
            LayoutWire(1, 1, 99, 1, WireColor.RED),
            LayoutWire(99, 1, 2, 1, WireColor.RED),
        )
    else:
        entities.append(ConstantCombinator(3))
        positions[3] = (2.0, 0.0)
        connections.append(_connection(1, 3))
        wires = (LayoutWire(1, 1, 3, 1, WireColor.RED),)

    if blocker_at_anchor:
        entities.append(ConstantCombinator(4))
        positions[4] = (-10.0, 0.0)

    circuit = PhysicalCircuit(
        "anchored-interface",
        entities=entities,
        connections=connections,
        inputs=inputs,
        outputs=outputs,
    )
    layout = Layout(circuit, positions, relays, wires, (), ())
    fixed = {} if marker_fixed_at is None else {1: marker_fixed_at}
    base = LayoutOptimizationProblem(
        layout,
        _lattice(),
        safe_wire_span=2.1,
        fixed_positions=fixed,
    )
    component = RigidComponentConstraint(
        "device",
        origin=(4.0, 0.0),
        members=(RigidComponentMember(2, (0.0, 0.0)),),
        footprints=(ComponentRegion(-0.5, -0.5, 0.5, 0.5),),
        access_points=(ComponentAccessPoint("west", (-0.5, 0.0)),),
    )
    problem = ComponentLayoutOptimizationProblem(base, (component,))
    validate_component_layout_problem(problem)
    return problem


def _anchored_problem(
    component_problem: ComponentLayoutOptimizationProblem,
    *,
    direction: str = "input",
    port: str = "public",
    access_point: str = "west",
    anchor: tuple[float, float] = (-10.0, 0.0),
) -> AnchoredInterfaceLayoutProblem:
    return AnchoredInterfaceLayoutProblem(
        component_problem,
        (
            PublicPortAnchorConstraint(
                "device-public",
                direction,  # type: ignore[arg-type]
                port,
                "device",
                access_point,
                anchor,
                max_detour_tiles=3,
            ),
        ),
    )


def test_distant_public_anchor_is_pinned_before_fresh_routing() -> None:
    original = _component_problem()
    problem = _anchored_problem(original)

    result = route_anchored_interfaces_transactionally(problem)

    assert result.succeeded
    assert result.failure is None
    assert result.after.relay_count >= 1
    layout = result.problem.component_problem.layout_problem.layout
    fixed = result.problem.component_problem.layout_problem.fixed_positions
    assert layout.positions[1] == (-10.0, 0.0)
    assert fixed[1] == (-10.0, 0.0)
    assert 99 not in layout.positions
    assert len(result.reservations) == 1
    reservation = result.reservations[0]
    assert reservation.relay_ids
    for relay_id, position in zip(
        reservation.relay_ids,
        reservation.relay_positions,
        strict=True,
    ):
        assert layout.positions[relay_id] == position
        assert fixed[relay_id] == position
    validate_anchored_interface_routing(result.problem, result.reservations)


def test_reserved_interface_relays_survive_later_component_optimization() -> None:
    result = route_anchored_interfaces_transactionally(_anchored_problem(_component_problem()))
    assert result.succeeded
    reservation = result.reservations[0]

    optimized = optimize_component_layout(
        result.problem.component_problem,
        options=PlacementOptions(iterations=128, restarts=1),
    )

    for relay_id, position in zip(
        reservation.relay_ids,
        reservation.relay_positions,
        strict=True,
    ):
        assert optimized.layout.positions[relay_id] == position
    assert optimized.layout.positions[1] == (-10.0, 0.0)


def test_output_port_uses_the_same_anchored_routing_contract() -> None:
    problem = _anchored_problem(_component_problem(output=True), direction="output")

    result = route_anchored_interfaces_transactionally(problem)

    assert result.succeeded
    assert result.problem.component_problem.layout_problem.layout.positions[1] == (-10.0, 0.0)
    validate_anchored_interface_routing(result.problem, result.reservations)


def test_anchor_collision_returns_exact_original_fallback() -> None:
    original = _component_problem(blocker_at_anchor=True)
    problem = _anchored_problem(original)

    result = route_anchored_interfaces_transactionally(problem)

    assert not result.succeeded
    assert result.problem is problem
    assert result.before == result.after
    assert result.failure is not None
    assert "overlap" in result.failure
    validate_component_layout_problem(result.problem.component_problem)


def test_existing_conflicting_fixed_marker_returns_exact_fallback() -> None:
    original = _component_problem(marker_fixed_at=(0.0, 0.0))
    problem = _anchored_problem(original)

    result = route_anchored_interfaces_transactionally(problem)

    assert not result.succeeded
    assert result.problem is problem
    assert result.failure is not None
    assert "already fixed" in result.failure
    validate_component_layout_problem(result.problem.component_problem)


def test_public_net_must_enter_the_declared_component() -> None:
    problem = _anchored_problem(_component_problem(port_enters_component=False))

    result = route_anchored_interfaces_transactionally(problem)

    assert not result.succeeded
    assert result.failure is not None
    assert "does not enter component" in result.failure


def test_unknown_access_point_is_rejected_transactionally() -> None:
    problem = _anchored_problem(_component_problem(), access_point="missing")

    result = route_anchored_interfaces_transactionally(problem)

    assert not result.succeeded
    assert result.failure is not None
    assert "has no access point" in result.failure


def test_anchored_interface_routing_is_deterministic() -> None:
    problem = _anchored_problem(_component_problem())

    left = route_anchored_interfaces_transactionally(problem)
    right = route_anchored_interfaces_transactionally(problem)

    assert left == right


def test_exact_anchor_may_be_changed_by_replacing_only_the_constraint() -> None:
    original = _anchored_problem(_component_problem())
    changed = replace(
        original,
        interfaces=(replace(original.interfaces[0], anchor_position=(-12.0, 0.0)),),
    )

    result = route_anchored_interfaces_transactionally(changed)

    assert result.succeeded
    assert result.problem.component_problem.layout_problem.layout.positions[1] == (-12.0, 0.0)
