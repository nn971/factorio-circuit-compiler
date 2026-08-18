from __future__ import annotations

from itertools import combinations

import pytest

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.synthesis import open_vector
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options


def _constant_fixture(
    entity_count: int,
    net_endpoints: tuple[tuple[int, int], ...],
) -> abstract.AbstractPhysicalCircuit:
    circuit = abstract.AbstractPhysicalCircuit("safe_crossbar")
    circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True)
        for entity_id in range(1, entity_count + 1)
    )
    single = abstract.Connector.SINGLE
    circuit.nets.extend(
        abstract.AbstractNet(
            net_id,
            (),
            (abstract.Endpoint(left, single), abstract.Endpoint(right, single)),
        )
        for net_id, (left, right) in enumerate(net_endpoints, start=1)
    )
    return circuit


def _mixed_color_fixture() -> abstract.AbstractPhysicalCircuit:
    circuit = _constant_fixture(3, ((1, 2), (2, 3)))
    # The two electrical networks touch the same constant-combinator connector and must therefore
    # receive opposite Factorio wire colors instead of being coalesced.
    circuit.net_conflicts.append(abstract.NetConflict(1, 2, "exercise both crossbar half-planes"))
    return circuit


def _tap_y_by_group(layout: object) -> dict[int, float]:
    # Keep this helper local to the tests rather than exposing layout-internal routing metadata.
    relays = layout.relays
    result: dict[int, float] = {}
    for relay in relays:
        if " tap " not in relay.description:
            continue
        group = int(relay.description.rsplit(" ", 1)[1])
        previous = result.setdefault(group, relay.position[1])
        assert previous == relay.position[1]
    return result


def test_safe_crossbar_constructs_mixed_color_layout_without_router_search(monkeypatch) -> None:
    def fail_router(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("heuristic route_wires must not run for safe-crossbar")

    monkeypatch.setattr(open_vector, "route_wires", fail_router)
    layout = synthesize_vector_layout(
        _mixed_color_fixture(),
        safe_wire_span=7.0,
        placement=safe_crossbar_options(),
    )

    groups = layout.coalesced_net_groups
    colors = layout.assigned_net_colors
    group_colors = {groups[net_id]: color for net_id, color in colors.items()}
    assert set(group_colors.values()) == {WireColor.RED, WireColor.GREEN}
    assert layout.relays

    for relay in layout.relays:
        group = int(relay.description.rsplit(" ", 1)[1])
        if group_colors[group] is WireColor.RED:
            assert relay.position[1] < 0
        else:
            assert relay.position[1] > 0

    # The constructive lattice keeps every pair of 1x1 relay collision boxes apart. Use the same
    # 0.1 safety margin as the normal router's clearance model.
    for left, right in combinations(layout.relays, 2):
        dx = abs(left.position[0] - right.position[0])
        dy = abs(left.position[1] - right.position[1])
        assert dx >= 1.1 or dy >= 1.1


def test_disjoint_same_color_nets_reuse_one_bus_track() -> None:
    layout = synthesize_vector_layout(
        _constant_fixture(4, ((1, 2), (3, 4))),
        safe_wire_span=7.0,
        placement=safe_crossbar_options(),
    )

    groups = layout.coalesced_net_groups
    colors = layout.assigned_net_colors
    assert colors[1] == colors[2]
    assert groups[1] != groups[2]

    tap_y = _tap_y_by_group(layout)
    assert tap_y[groups[1]] == tap_y[groups[2]]


def test_overlapping_same_color_nets_use_separate_bus_tracks() -> None:
    layout = synthesize_vector_layout(
        _constant_fixture(4, ((1, 3), (2, 4))),
        safe_wire_span=7.0,
        placement=safe_crossbar_options(),
    )

    groups = layout.coalesced_net_groups
    colors = layout.assigned_net_colors
    assert colors[1] == colors[2]
    assert groups[1] != groups[2]

    tap_y = _tap_y_by_group(layout)
    assert tap_y[groups[1]] != tap_y[groups[2]]


def test_many_disjoint_nets_stay_linear_on_one_track() -> None:
    net_endpoints = tuple((2 * index + 1, 2 * index + 2) for index in range(20))
    layout = synthesize_vector_layout(
        _constant_fixture(40, net_endpoints),
        safe_wire_span=7.0,
        placement=safe_crossbar_options(),
    )

    # Every two-endpoint interval is six tiles away from the next interval. They therefore reuse
    # track zero. Each group needs two taps plus one ordinary bus relay: exactly three relays.
    assert len(layout.relays) == 60
    assert len(set(_tap_y_by_group(layout).values())) == 1


def test_safe_crossbar_is_deterministic() -> None:
    first = synthesize_vector_layout(
        _mixed_color_fixture(),
        safe_wire_span=7.0,
        placement=safe_crossbar_options(),
    )
    second = synthesize_vector_layout(
        _mixed_color_fixture(),
        safe_wire_span=7.0,
        placement=safe_crossbar_options(),
    )

    assert first.positions == second.positions
    assert first.relays == second.relays
    assert first.wires == second.wires
    assert first.net_colors == second.net_colors
    assert first.net_groups == second.net_groups


def test_safe_crossbar_rejects_too_short_configured_wire_span() -> None:
    with pytest.raises(ValueError, match="safe-crossbar requires"):
        synthesize_vector_layout(
            _mixed_color_fixture(),
            safe_wire_span=6.0,
            placement=safe_crossbar_options(),
        )
