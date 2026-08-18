from __future__ import annotations

from itertools import combinations

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    ConstantCombinator,
    InputPort,
    Operand,
    OutputPort,
    PhysicalCircuit,
    SignalId,
    WireColor,
)
from factorio_circuit.synthesis import open_vector
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options
from factorio_circuit.synthesis.safe_folded_crossbar import (
    _bus_y,
    _cut_crossing_counts,
    _folded_ordered_entities,
    _plan_folded_crossbar,
    _portal_x_values,
    _vertical_regular_relay_count,
    safe_folded_crossbar_options,
)


def _long_fixture(entity_count: int = 64) -> abstract.AbstractPhysicalCircuit:
    circuit = abstract.AbstractPhysicalCircuit("safe_folded_crossbar")
    circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True)
        for entity_id in range(1, entity_count + 1)
    )
    single = abstract.Connector.SINGLE
    circuit.nets.append(
        abstract.AbstractNet(
            1,
            (),
            (abstract.Endpoint(1, single), abstract.Endpoint(entity_count, single)),
        )
    )
    return circuit


def test_folded_safe_crossbar_crosses_rows_without_router_search(monkeypatch) -> None:
    def fail_router(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("heuristic route_wires must not run for safe-folded-crossbar")

    monkeypatch.setattr(open_vector, "route_wires", fail_router)
    layout = synthesize_vector_layout(
        _long_fixture(),
        safe_wire_span=7.0,
        placement=safe_folded_crossbar_options(),
    )

    real_y = {layout.positions[entity.id][1] for entity in layout.circuit.entities}
    assert len(real_y) > 1
    assert any("fold stitch" in relay.description for relay in layout.relays)

    # Layout relays are 1x1 constant combinators, so centers one tile apart are legal
    # and are the intended packed density for adjacent bus tracks / portal columns.
    for left, right in combinations(layout.relays, 2):
        dx = abs(left.position[0] - right.position[0])
        dy = abs(left.position[1] - right.position[1])
        assert dx >= 1.0 - 1e-9 or dy >= 1.0 - 1e-9

    # Factorio can shift one placement-coordinate phase consistently, but mixing integer and
    # half-integer 1x1 relay centers can make two intended relays snap onto one tile. Keep every
    # routing relay on the same integer blueprint lattice.
    assert all(
        abs(coordinate - round(coordinate)) < 1e-9
        for relay in layout.relays
        for coordinate in relay.position
    )


def test_folded_safe_crossbar_is_deterministic() -> None:
    first = synthesize_vector_layout(
        _long_fixture(),
        safe_wire_span=7.0,
        placement=safe_folded_crossbar_options(),
    )
    second = synthesize_vector_layout(
        _long_fixture(),
        safe_wire_span=7.0,
        placement=safe_folded_crossbar_options(),
    )

    assert first.positions == second.positions
    assert first.relays == second.relays
    assert first.wires == second.wires


def test_folded_entity_rows_use_three_tile_pitch_and_safe_feeder_residues() -> None:
    signal = SignalId("virtual", "signal-A")
    physical = PhysicalCircuit("dense_entity_rows")
    physical.entities.extend(
        ArithmeticCombinator(
            entity_id,
            "+",
            Operand(signal=signal),
            Operand(constant=1),
            output_each=False,
            output_signal=signal,
        )
        for entity_id in range(1, 13)
    )

    plan = _plan_folded_crossbar(physical, {}, {})

    assert plan.entity_rows > 1
    assert plan.entities_per_row % 2 == 1
    rows: dict[float, list[float]] = {}
    for x, y in plan.positions.values():
        rows.setdefault(y, []).append(x)
        # Two-port combinators place feeders two tiles to either side. With centers
        # alternating between x = 0 and 3 (mod 6), neither feeder can hit x = 0 (mod 6).
        assert abs((x - 2.0) % 6.0) > 1e-9
        assert abs((x + 2.0) % 6.0) > 1e-9
    for xs in rows.values():
        xs.sort()
        assert all(abs(right - left - 3.0) < 1e-9 for left, right in zip(xs, xs[1:], strict=False))


def test_folded_bus_tracks_pack_one_tile_apart_on_integer_relay_lattice() -> None:
    red_rows = [_bus_y(0.0, WireColor.RED, track) for track in range(8)]
    green_rows = [_bus_y(0.0, WireColor.GREEN, track) for track in range(8)]
    red_gaps = [abs(right - left) for left, right in zip(red_rows, red_rows[1:], strict=False)]
    green_gaps = [
        abs(right - left) for left, right in zip(green_rows, green_rows[1:], strict=False)
    ]

    assert all(abs(gap - 1.0) < 1e-9 for gap in red_gaps)
    assert all(abs(gap - 1.0) < 1e-9 for gap in green_gaps)
    assert all(abs(row - round(row)) < 1e-9 for row in (*red_rows, *green_rows))


def test_fold_stitch_count_excludes_bus_taps_on_regular_lattice() -> None:
    # With integer bus rows, a fold tap can itself land on y = 0 (mod 6). Construction
    # reuses that tap and adds only the regular stitch relays strictly between the two taps.
    assert _vertical_regular_relay_count(-6.0, 24.0) == 4
    assert _vertical_regular_relay_count(-5.0, 24.0) == 4
    assert _vertical_regular_relay_count(-6.0, 23.0) == 4


def test_cut_crossing_counts_match_route_spans() -> None:
    single = abstract.Connector.SINGLE
    entity_index = {entity_id: entity_id - 1 for entity_id in range(1, 7)}
    route_specs = {
        1: (
            WireColor.RED,
            (abstract.Endpoint(1, single), abstract.Endpoint(6, single)),
        ),
        2: (
            WireColor.GREEN,
            (abstract.Endpoint(2, single), abstract.Endpoint(4, single)),
        ),
    }

    assert _cut_crossing_counts(6, route_specs, entity_index) == (0, 1, 2, 2, 1, 1, 0)


def test_folded_portals_pack_adjacent_tiles_but_skip_row_bus_lattice() -> None:
    right_side = [_portal_x_values(21, boundary=0, ordinal=ordinal) for ordinal in range(20)]
    left_side = [_portal_x_values(21, boundary=1, ordinal=ordinal) for ordinal in range(20)]
    gaps = [right - left for left, right in zip(right_side, right_side[1:], strict=False)]

    assert min(gaps) == 1.0
    assert max(gaps) == 2.0
    assert all(abs(value % 6.0) > 1e-9 for value in right_side)
    assert all(abs(value % 6.0) > 1e-9 for value in left_side)


def test_folded_row_track_assignment_uses_actual_portal_extended_segments() -> None:
    physical = PhysicalCircuit("folded_segment_coloring")
    physical.entities.extend(ConstantCombinator(entity_id) for entity_id in range(1, 121))

    single = abstract.Connector.SINGLE
    endpoints_by_group: dict[int, set[abstract.Endpoint]] = {}
    colors_by_group: dict[int, WireColor] = {}
    # These virtual intervals are mutually disjoint, so the linear safe crossbar can reuse a
    # single track. After folding, neighbouring intervals can occupy the same physical entity row
    # while one or both row segments extend to fold portals. The folded planner must color those
    # actual row segments, rather than trusting the linear track identity.
    for group, (left, right) in enumerate(
        ((1, 20), (21, 40), (41, 60), (61, 80), (81, 100), (101, 120)),
        start=1,
    ):
        endpoints_by_group[group] = {
            abstract.Endpoint(left, single),
            abstract.Endpoint(right, single),
        }
        colors_by_group[group] = WireColor.RED

    plan = _plan_folded_crossbar(physical, endpoints_by_group, colors_by_group)

    by_row_track: dict[tuple[int, WireColor, int], list[tuple[float, float, int]]] = {}
    for key, segment in plan.segments.items():
        track = plan.segment_tracks[key]
        by_row_track.setdefault((segment.row, segment.color, track), []).append(
            (segment.min_x, segment.max_x, segment.group)
        )

    for intervals in by_row_track.values():
        intervals.sort()
        for left, right in zip(intervals, intervals[1:], strict=False):
            assert left[1] + 1.1 <= right[0] + 1e-9


def test_folded_and_linear_safe_strategies_remain_independent() -> None:
    folded = safe_folded_crossbar_options()
    linear = safe_crossbar_options()

    assert str(folded.strategy) == "safe-folded-crossbar"
    assert str(linear.strategy) == "safe-crossbar"


def test_folded_order_places_public_inputs_and_outputs_together_first() -> None:
    circuit = PhysicalCircuit("front_panel")
    circuit.entities.extend(ConstantCombinator(entity_id) for entity_id in range(1, 7))
    circuit.inputs.append(InputPort("movement", marker_entity=5, signal=None))
    circuit.outputs.append(OutputPort("framebuffer", marker_entity=6, signal=None, phase=0))

    ordered = _folded_ordered_entities(circuit)

    assert [entity.id for entity in ordered[:2]] == [5, 6]
    assert {entity.id for entity in ordered[2:]} == {1, 2, 3, 4}
