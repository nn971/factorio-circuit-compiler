from __future__ import annotations

from itertools import combinations
from typing import Any, cast

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.synthesis import open_vector
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.placement import PlacementOptions


def _safe_options() -> PlacementOptions:
    return PlacementOptions(strategy=cast(Any, "safe-crossbar"), restarts=1)


def _mixed_color_fixture() -> abstract.AbstractPhysicalCircuit:
    circuit = abstract.AbstractPhysicalCircuit("safe_crossbar")
    circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True)
        for entity_id in (1, 2, 3)
    )
    single = abstract.Connector.SINGLE
    circuit.nets.extend(
        [
            abstract.AbstractNet(
                1,
                (),
                (abstract.Endpoint(1, single), abstract.Endpoint(2, single)),
            ),
            abstract.AbstractNet(
                2,
                (),
                (abstract.Endpoint(2, single), abstract.Endpoint(3, single)),
            ),
        ]
    )
    # The two electrical networks touch the same constant-combinator connector and must therefore
    # receive opposite Factorio wire colors instead of being coalesced.
    circuit.net_conflicts.append(abstract.NetConflict(1, 2, "exercise both crossbar half-planes"))
    return circuit


def test_safe_crossbar_constructs_mixed_color_layout_without_router_search(monkeypatch) -> None:
    def fail_router(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("heuristic route_wires must not run for safe-crossbar")

    monkeypatch.setattr(open_vector, "route_wires", fail_router)
    layout = synthesize_vector_layout(
        _mixed_color_fixture(),
        safe_wire_span=7.0,
        placement=_safe_options(),
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

    # The constructive lattice keeps every pair of 1x1 relay collision boxes apart.  Use the same
    # 0.1 safety margin as the normal router's clearance model.
    for left, right in combinations(layout.relays, 2):
        dx = abs(left.position[0] - right.position[0])
        dy = abs(left.position[1] - right.position[1])
        assert dx >= 1.1 or dy >= 1.1


def test_safe_crossbar_is_deterministic() -> None:
    first = synthesize_vector_layout(
        _mixed_color_fixture(),
        safe_wire_span=7.0,
        placement=_safe_options(),
    )
    second = synthesize_vector_layout(
        _mixed_color_fixture(),
        safe_wire_span=7.0,
        placement=_safe_options(),
    )

    assert first.positions == second.positions
    assert first.relays == second.relays
    assert first.wires == second.wires
    assert first.net_colors == second.net_colors
    assert first.net_groups == second.net_groups


def test_safe_crossbar_rejects_too_short_configured_wire_span() -> None:
    import pytest

    with pytest.raises(ValueError, match="safe-crossbar requires"):
        synthesize_vector_layout(
            _mixed_color_fixture(),
            safe_wire_span=6.0,
            placement=_safe_options(),
        )
