from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.component_geometry import (
    ComponentLayoutOptimizationProblem,
    ComponentRegion,
    RigidComponentConstraint,
    RigidComponentMember,
    validate_component_layout_problem,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
)
from factorio_circuit.synthesis.rigid_component_translation import (
    RigidTranslationOptions,
    optimize_rigid_component_translations,
    translate_rigid_component_transactionally,
)


def _lattice(width: int = 14, height: int = 4) -> LegalPlacementLattice:
    unit_sites = tuple((float(x), float(y)) for y in range(height) for x in range(width))
    return LegalPlacementLattice(unit_sites=unit_sites, wide_sites=())


def _connection(left: int, right: int) -> WireConnection:
    return WireConnection(
        WireEndpoint(left, Connector.SINGLE),
        WireEndpoint(right, Connector.SINGLE),
        WireColor.RED,
    )


def _simple_problem(
    *,
    allowed_origins: tuple[tuple[float, float], ...] | None = ((1.0, 1.0), (7.0, 1.0)),
    keepouts: tuple[ComponentRegion, ...] = (),
) -> ComponentLayoutOptimizationProblem:
    circuit = PhysicalCircuit(
        "rigid-translation",
        entities=[ConstantCombinator(1), ConstantCombinator(2), ConstantCombinator(3)],
        connections=[_connection(2, 3)],
    )
    layout = Layout(
        circuit,
        {1: (1.0, 1.0), 2: (2.0, 1.0), 3: (4.0, 1.0)},
        (),
        (LayoutWire(2, 1, 3, 1, WireColor.RED),),
        (),
        (),
    )
    base = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=2.1)
    component = RigidComponentConstraint(
        "cell",
        origin=(1.0, 1.0),
        members=(
            RigidComponentMember(1, (0.0, 0.0)),
            RigidComponentMember(2, (1.0, 0.0)),
        ),
        footprints=(ComponentRegion(-0.5, -0.5, 1.5, 0.5),),
        keepouts=keepouts,
        allowed_origins=allowed_origins,
    )
    problem = ComponentLayoutOptimizationProblem(base, (component,))
    validate_component_layout_problem(problem)
    return problem


def test_transaction_moves_all_members_rigidly_and_rebuilds_routing() -> None:
    problem = _simple_problem()

    result = translate_rigid_component_transactionally(problem, "cell", (7.0, 1.0))

    assert result.succeeded
    assert result.failure is None
    assert result.problem.components[0].origin == (7.0, 1.0)
    positions = result.problem.layout_problem.layout.positions
    assert positions[1] == (7.0, 1.0)
    assert positions[2] == (8.0, 1.0)
    assert positions[2][0] - positions[1][0] == 1.0
    assert result.after.relay_count >= 1
    validate_component_layout_problem(result.problem)


def test_transaction_rejects_non_integral_translation_with_exact_fallback() -> None:
    problem = _simple_problem(allowed_origins=None)

    result = translate_rigid_component_transactionally(problem, "cell", (1.5, 1.0))

    assert not result.succeeded
    assert result.problem is problem
    assert result.before == result.after
    assert result.failure is not None
    assert "integral number of tiles" in result.failure
    validate_component_layout_problem(result.problem)


def test_transaction_rejects_origin_outside_declared_pose_set() -> None:
    problem = _simple_problem()

    result = translate_rigid_component_transactionally(problem, "cell", (6.0, 1.0))

    assert not result.succeeded
    assert result.problem is problem
    assert result.failure is not None
    assert "not declared" in result.failure


def test_transaction_rejects_component_collision_before_routing() -> None:
    problem = _simple_problem(allowed_origins=((1.0, 1.0), (3.0, 1.0)))

    result = translate_rigid_component_transactionally(problem, "cell", (3.0, 1.0))

    assert not result.succeeded
    assert result.problem is problem
    assert result.failure is not None
    assert "overlap" in result.failure
    validate_component_layout_problem(result.problem)


def test_fresh_relays_respect_translated_component_keepout() -> None:
    problem = _simple_problem(
        keepouts=(ComponentRegion(-2.5, -0.5, -1.5, 0.5),),
    )

    result = translate_rigid_component_transactionally(problem, "cell", (7.0, 1.0))

    assert result.succeeded
    keepout = result.problem.components[0].absolute_keepouts()[0]
    for relay in result.problem.layout_problem.layout.relays:
        assert not keepout.overlaps_box(relay.position, (0.5, 0.5))
    validate_component_layout_problem(result.problem)


def _optimizable_problem() -> ComponentLayoutOptimizationProblem:
    circuit = PhysicalCircuit(
        "rigid-translation-optimize",
        entities=[ConstantCombinator(1), ConstantCombinator(2), ConstantCombinator(3)],
        connections=[_connection(2, 3)],
    )
    relays = (
        LayoutRelay(4, (7.0, 1.0), "seed"),
        LayoutRelay(5, (5.0, 1.0), "seed"),
        LayoutRelay(6, (3.0, 1.0), "seed"),
    )
    wires = (
        LayoutWire(2, 1, 4, 1, WireColor.RED),
        LayoutWire(4, 1, 5, 1, WireColor.RED),
        LayoutWire(5, 1, 6, 1, WireColor.RED),
        LayoutWire(6, 1, 3, 1, WireColor.RED),
    )
    layout = Layout(
        circuit,
        {
            1: (8.0, 1.0),
            2: (9.0, 1.0),
            3: (1.0, 1.0),
            4: (7.0, 1.0),
            5: (5.0, 1.0),
            6: (3.0, 1.0),
        },
        relays,
        wires,
        (),
        (),
    )
    base = LayoutOptimizationProblem(layout, _lattice(), safe_wire_span=2.1)
    component = RigidComponentConstraint(
        "cell",
        origin=(8.0, 1.0),
        members=(
            RigidComponentMember(1, (0.0, 0.0)),
            RigidComponentMember(2, (1.0, 0.0)),
        ),
        footprints=(ComponentRegion(-0.5, -0.5, 1.5, 0.5),),
        allowed_origins=((8.0, 1.0), (2.0, 1.0)),
    )
    problem = ComponentLayoutOptimizationProblem(base, (component,))
    validate_component_layout_problem(problem)
    return problem


def test_bounded_optimizer_accepts_strictly_better_rigid_pose() -> None:
    problem = _optimizable_problem()

    result = optimize_rigid_component_translations(
        problem,
        options=RigidTranslationOptions(max_passes=2, max_candidates_per_component=8),
    )

    assert result.accepted_moves == (("cell", (8.0, 1.0), (2.0, 1.0)),)
    assert result.problem.components[0].origin == (2.0, 1.0)
    assert result.after.objective < result.before.objective
    assert result.after.relay_count == 0
    assert result.evaluated_candidates >= 1
    assert result.feasible_candidates >= 1
    validate_component_layout_problem(result.problem)


def test_bounded_optimizer_skips_unbounded_origin_domain() -> None:
    problem = _simple_problem(allowed_origins=None)

    result = optimize_rigid_component_translations(
        problem,
        options=RigidTranslationOptions(max_passes=3),
    )

    assert result.problem is problem
    assert result.before == result.after
    assert result.evaluated_candidates == 0
    assert result.accepted_moves == ()
    assert any("no finite allowed_origins" in item for item in result.diagnostics)


def test_rigid_translation_optimization_is_deterministic() -> None:
    problem = _optimizable_problem()
    options = RigidTranslationOptions(max_passes=2, max_candidates_per_component=8)

    left = optimize_rigid_component_translations(problem, options=options)
    right = optimize_rigid_component_translations(problem, options=options)

    assert left == right
