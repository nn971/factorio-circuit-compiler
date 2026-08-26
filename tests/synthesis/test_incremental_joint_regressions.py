from dataclasses import replace

import pytest

from factorio_circuit.blueprint.routing import BlueprintRelay, RoutedWire, RoutingPlan
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ConstantCombinator,
    InputPort,
    OutputPort,
    PhysicalCircuit,
    WireColor,
)
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis.open_vector import _retryable_layout_error
from factorio_circuit.synthesis.placement import PlacementOptions


def _constant_state_with_relay() -> exact._JointState:
    physical = PhysicalCircuit(
        "relay_snapshot",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2)],
    )
    endpoints = (
        abstract.Endpoint(1, abstract.Connector.SINGLE),
        abstract.Endpoint(2, abstract.Connector.SINGLE),
    )
    return exact._JointState(
        circuit=physical,
        endpoints_by_group={1: endpoints},
        colors_by_group={1: WireColor.RED},
        positions={1: (0.0, 0.0), 2: (3.0, 0.0)},
        relay_positions={3: (1.5, 0.0)},
        relay_groups={3: frozenset({1})},
        safe_span=2.0,
        forbidden_areas=(),
    )


def test_feasible_topology_materializes_current_relay_positions() -> None:
    state = _constant_state_with_relay()
    stale = RoutingPlan(
        relays=(BlueprintRelay(3, (1.0, 0.0), "relay"),),
        wires=(
            RoutedWire(1, 1, 3, 1, WireColor.RED),
            RoutedWire(3, 1, 2, 1, WireColor.RED),
        ),
    )

    topology = incremental._FeasibleTopology.build(state, stale)

    assert topology.routing.relays == (BlueprintRelay(3, (1.5, 0.0), "relay"),)
    incremental._validate_joint_clearance(state, topology.routing)


def test_joint_clearance_rejects_a_stale_serialized_relay_position() -> None:
    state = _constant_state_with_relay()
    stale = RoutingPlan(
        relays=(BlueprintRelay(3, (1.0, 0.0), "relay"),),
        wires=(
            RoutedWire(1, 1, 3, 1, WireColor.RED),
            RoutedWire(3, 1, 2, 1, WireColor.RED),
        ),
    )

    with pytest.raises(ValueError, match="relay positions disagree"):
        incremental._validate_joint_clearance(state, stale)


def test_porous_bootstrap_reanchors_automatic_io_after_grid_expansion() -> None:
    physical = PhysicalCircuit(
        "expanded_io",
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)],
        inputs=[InputPort("in", 1, None)],
        outputs=[OutputPort("out", 2, None, 0)],
    )
    options = PlacementOptions(
        iterations=0,
        reserve_corridors=False,
        target_fill=0.6,
    )
    initial = incremental.base_placement._candidate_grid(4, 1, options)
    expanded = incremental._expanded_bootstrap_grid(physical, initial, options)
    initial_io = incremental.base_placement._automatic_io_anchors(physical, initial.bounds)
    expanded_io = incremental.base_placement._automatic_io_anchors(physical, expanded.bounds)
    preferred = {**initial_io, 3: initial.unit_slots[0]}

    positions = incremental._porous_bootstrap_positions(
        physical,
        preferred,
        options,
        expanded,
    )

    assert positions is not None
    assert positions[1] == expanded_io[1]
    assert positions[2] == expanded_io[2]
    assert positions[2] != initial_io[2]


def test_porous_bootstrap_preserves_explicit_io_anchor_after_expansion() -> None:
    explicit_output = (100.0, 12.0)
    physical = PhysicalCircuit(
        "expanded_explicit_io",
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)],
        inputs=[InputPort("in", 1, None)],
        outputs=[OutputPort("out", 2, None, 0)],
    )
    base_options = PlacementOptions(
        iterations=0,
        reserve_corridors=False,
        target_fill=0.6,
    )
    initial = incremental.base_placement._candidate_grid(4, 1, base_options)
    expanded = incremental._expanded_bootstrap_grid(physical, initial, base_options)
    options = replace(base_options, anchors={2: explicit_output})
    preferred = {
        **incremental.base_placement._automatic_io_anchors(physical, initial.bounds),
        2: explicit_output,
        3: initial.unit_slots[0],
    }

    positions = incremental._porous_bootstrap_positions(
        physical,
        preferred,
        options,
        expanded,
    )

    assert positions is not None
    assert positions[2] == explicit_output


def test_porous_bootstrap_reserves_periodic_connected_routing_channels() -> None:
    physical = PhysicalCircuit(
        "channelized_bootstrap",
        entities=[ConstantCombinator(entity_id) for entity_id in range(1, 41)],
    )
    options = PlacementOptions(
        anchor_io=False,
        iterations=0,
        reserve_corridors=False,
        target_fill=0.6,
    )
    grid = incremental.base_placement._candidate_grid(48, 1, options)
    preferred = {
        entity.id: grid.unit_slots[index] for index, entity in enumerate(physical.entities)
    }

    positions = incremental._porous_bootstrap_positions(
        physical,
        preferred,
        options,
        grid,
    )

    assert positions is not None
    channel_x = set(
        grid.x_positions[
            incremental._ROUTING_CHANNEL_COLUMN_STRIDE
            - 1 :: incremental._ROUTING_CHANNEL_COLUMN_STRIDE
        ]
    )
    channel_unit_x = {value for center in channel_x for value in (center - 0.5, center + 0.5)}
    channel_y = set(
        grid.y_positions[
            incremental._ROUTING_CHANNEL_ROW_STRIDE - 1 :: incremental._ROUTING_CHANNEL_ROW_STRIDE
        ]
    )
    assert channel_unit_x
    assert channel_y
    assert all(x not in channel_unit_x and y not in channel_y for x, y in positions.values())


def _automatic_interface_state() -> tuple[exact._JointState, incremental._FeasibleTopology]:
    physical = PhysicalCircuit(
        "automatic_interface",
        entities=[ConstantCombinator(entity_id, annotation_only=True) for entity_id in (1, 2, 3)],
        inputs=[InputPort("in", 1, None)],
        outputs=[OutputPort("out", 2, None, 0)],
    )
    state = exact._JointState(
        circuit=physical,
        endpoints_by_group={
            1: (
                abstract.Endpoint(1, abstract.Connector.SINGLE),
                abstract.Endpoint(3, abstract.Connector.SINGLE),
            ),
            2: (
                abstract.Endpoint(2, abstract.Connector.SINGLE),
                abstract.Endpoint(3, abstract.Connector.SINGLE),
            ),
        },
        colors_by_group={1: WireColor.RED, 2: WireColor.GREEN},
        positions={1: (-2.0, 0.0), 2: (8.0, 0.0), 3: (3.0, 0.0)},
        relay_positions={},
        relay_groups={},
        safe_span=7.0,
        forbidden_areas=(),
    )
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(
            relays=(),
            wires=(
                RoutedWire(1, 1, 3, 1, WireColor.RED),
                RoutedWire(2, 1, 3, 1, WireColor.GREEN),
            ),
        ),
    )
    return state, topology


def test_topology_rebuild_places_automatic_io_on_exact_body_perimeter() -> None:
    state, topology = _automatic_interface_state()
    options = PlacementOptions(
        iterations=0,
        reserve_corridors=False,
        target_fill=0.6,
    )
    grid = incremental.base_placement._candidate_grid(4, 1, options)

    rebuilt = incremental._rebuild_automatic_interface_topology(
        state,
        topology,
        options,
        grid,
    )

    assert state.positions == {1: (1.5, 0.0), 2: (4.5, 0.0), 3: (3.0, 0.0)}
    incremental.wire_routing.validate_wire_spans(
        rebuilt.routing.wires,
        state.positions,
        maximum_span=state.safe_span,
    )


def test_topology_rebuild_preserves_explicit_io_anchor() -> None:
    state, topology = _automatic_interface_state()
    explicit_output = (8.0, 2.0)
    state.positions[2] = explicit_output
    topology = incremental._FeasibleTopology.build(state, topology.routing)
    options = PlacementOptions(
        anchors={2: explicit_output},
        iterations=0,
        reserve_corridors=False,
        target_fill=0.6,
    )
    grid = incremental.base_placement._candidate_grid(4, 1, options)

    incremental._rebuild_automatic_interface_topology(
        state,
        topology,
        options,
        grid,
    )

    assert state.positions[2] == explicit_output
    assert state.positions[1] == (1.5, 1.0)


def test_failed_coarse_retopology_restores_prior_feasible_relays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _constant_state_with_relay()
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(
            relays=(BlueprintRelay(3, (1.5, 0.0), "relay"),),
            wires=(
                RoutedWire(1, 1, 3, 1, WireColor.RED),
                RoutedWire(3, 1, 2, 1, WireColor.RED),
            ),
        ),
    )
    options = PlacementOptions(
        anchor_io=False,
        iterations=0,
        reserve_corridors=False,
        target_fill=0.6,
    )
    grid = incremental.base_placement._candidate_grid(4, 1, options)

    def fail_rebuild(
        _state: exact._JointState,
        _grid: incremental.base_placement._GridGeometry,
    ) -> incremental._FeasibleTopology:
        raise ValueError("synthetic coarse rebuild failure")

    monkeypatch.setattr(incremental, "_construct_feasible_bootstrap", fail_rebuild)

    result = incremental._try_rebuild_annealed_topology(state, topology, grid)

    assert result is topology
    assert state.relay_positions == {3: (1.5, 0.0)}
    assert state.relay_groups == {3: frozenset({1})}


def test_bootstrap_rips_up_and_reorders_after_an_early_net_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = PhysicalCircuit(
        "bootstrap_reorder",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2, 3, 4)],
    )
    state = exact._JointState(
        circuit=physical,
        endpoints_by_group={
            1: (
                abstract.Endpoint(1, abstract.Connector.SINGLE),
                abstract.Endpoint(2, abstract.Connector.SINGLE),
            ),
            2: (
                abstract.Endpoint(3, abstract.Connector.SINGLE),
                abstract.Endpoint(4, abstract.Connector.SINGLE),
            ),
        },
        colors_by_group={1: WireColor.RED, 2: WireColor.GREEN},
        positions={
            1: (-0.5, 0.0),
            2: (1.5, 0.0),
            3: (-0.5, 2.0),
            4: (1.5, 2.0),
        },
        relay_positions={},
        relay_groups={},
        safe_span=1.1,
        forbidden_areas=(),
    )
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        target_fill=0.5,
        iterations=0,
    )
    grid = incremental.base_placement._candidate_grid(8, 1, options)
    calls: list[int] = []

    def fake_chain(
        _state: exact._JointState,
        group: int,
        _left: int,
        _right: int,
        _free_sites: set[tuple[float, float]],
        _workspace: incremental._RelayWorkspace,
    ) -> tuple[tuple[float, float], ...] | None:
        calls.append(group)
        if len(calls) == 1:
            return None
        return ((0.5, 0.0),) if group == 1 else ((0.5, 2.0),)

    monkeypatch.setattr(incremental, "_find_relay_chain", fake_chain)

    topology = incremental._construct_feasible_bootstrap(state, grid)

    assert calls == [1, 2, 1]
    assert len(state.relay_positions) == 2
    assert len(topology.routing.relays) == 2


def test_new_joint_bootstrap_failure_is_retryable() -> None:
    error = ValueError(
        "joint bootstrap is outside conservative wire reach or exhausted relay-site capacity"
    )

    assert _retryable_layout_error(error)


def test_rectangle_overflow_tracks_footprint_distance_beyond_envelope() -> None:
    state = _constant_state_with_relay()
    envelope = incremental._occupied_envelope(state)

    assert envelope == (-0.5, 3.5, -0.5, 0.5)
    assert incremental._rectangle_overflow(state, 1, (0.0, 0.0), envelope) == 0.0
    assert incremental._rectangle_overflow(state, 1, (-1.0, 0.0), envelope) == pytest.approx(1.0)
    assert incremental._rectangle_overflow(state, 2, (4.0, 0.0), envelope) == pytest.approx(1.0)
    assert incremental._rectangle_overflow(state, 3, (1.5, -1.0), envelope) == pytest.approx(1.0)


def test_rectangle_overflow_penalty_is_quadratic_and_local() -> None:
    state = _constant_state_with_relay()
    envelope = incremental._occupied_envelope(state)

    inside = incremental._rectangle_overflow(state, 2, (3.0, 0.0), envelope)
    one_row_below = incremental._rectangle_overflow(state, 2, (3.0, 1.0), envelope)
    two_rows_below = incremental._rectangle_overflow(state, 2, (3.0, 2.0), envelope)

    assert inside == 0.0
    assert one_row_below == pytest.approx(1.0)
    assert two_rows_below == pytest.approx(4.0)


def test_annealer_retains_exact_best_state_seen_inside_an_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = PhysicalCircuit(
        "mid_epoch_best",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2)],
    )
    state = exact._JointState(
        circuit=physical,
        endpoints_by_group={},
        colors_by_group={},
        positions={1: (0.0, 0.0), 2: (10.0, 0.0)},
        relay_positions={},
        relay_groups={},
        safe_span=100.0,
        forbidden_areas=(),
    )
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(relays=(), wires=()),
    )
    options = PlacementOptions(
        anchor_io=False,
        iterations=2,
        reserve_corridors=False,
        target_fill=0.6,
    )
    grid = incremental.base_placement._candidate_grid(8, 1, options)
    proposed = iter(((1.0, 0.0), (2.0, 0.0)))
    exact_scores = iter(
        (
            (0, 100.0, 100.0),
            (0, 50.0, 50.0),
            (0, 80.0, 80.0),
        )
    )
    first_accepted_positions: dict[int, tuple[float, float]] = {}

    monkeypatch.setattr(
        incremental,
        "_proposed_position",
        lambda *_args, **_kwargs: next(proposed),
    )
    monkeypatch.setattr(incremental, "_position_is_legal", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(incremental, "_rectangle_overflow", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(exact, "_compactness", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        incremental,
        "_exact_score",
        lambda *_args, **_kwargs: (0, 80.0, 80.0),
    )

    def accepted_score(
        observed_state: exact._JointState,
        _topology: incremental._FeasibleTopology,
        _center: tuple[float, float],
    ) -> tuple[int, float, float]:
        score = next(exact_scores)
        if score == (0, 50.0, 50.0):
            first_accepted_positions.update(observed_state.positions)
        return score

    # The initial exact best is supplied separately so the helper sequence describes accepted moves.
    initial_calls = 0

    def exact_score(
        _state: exact._JointState,
        _topology: incremental._FeasibleTopology,
        _center: tuple[float, float],
    ) -> tuple[int, float, float]:
        nonlocal initial_calls
        initial_calls += 1
        return (0, 100.0, 100.0) if initial_calls == 1 else (0, 80.0, 80.0)

    monkeypatch.setattr(incremental, "_exact_score", exact_score)
    monkeypatch.setattr(incremental, "_accepted_move_exact_score", accepted_score)

    incremental._anneal_feasible(state, topology, options, grid)

    assert first_accepted_positions
    assert state.positions == first_accepted_positions
