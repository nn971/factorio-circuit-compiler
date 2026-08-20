import pytest

from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    AbstractSignal,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.synthesis.signal_coloring import (
    allocate_abstract_signals_dsat,
    color_interference_graph_dsat,
)


def _palette(size: int) -> tuple[SignalId, ...]:
    return tuple(SignalId("virtual", f"signal-{index}") for index in range(size))


def test_dsat_colors_crown_graph_with_two_signals() -> None:
    # K3,3 minus a perfect matching.  The stable vertex ids deliberately interleave the two sides;
    # dynamic saturation should still recover the bipartite two-coloring deterministically.
    left = (1, 3, 5)
    right = (2, 4, 6)
    matching = {(1, 2), (3, 4), (5, 6)}
    adjacency = {vertex: set() for vertex in (*left, *right)}
    for lvertex in left:
        for rvertex in right:
            if (lvertex, rvertex) in matching:
                continue
            adjacency[lvertex].add(rvertex)
            adjacency[rvertex].add(lvertex)

    allocation = color_interference_graph_dsat(adjacency, _palette(2))

    assert len(set(allocation.values())) == 2
    for vertex, neighbors in adjacency.items():
        assert all(allocation[vertex] != allocation[neighbor] for neighbor in neighbors)


def test_dsat_exhaustion_reports_graph_pressure() -> None:
    adjacency = {
        1: {2, 3},
        2: {1, 3},
        3: {1, 2},
    }

    with pytest.raises(ValueError, match=r"palette=2.*saturation=2.*colors_used=2"):
        color_interference_graph_dsat(adjacency, _palette(2))


def test_disconnected_abstract_lanes_reuse_one_concrete_signal() -> None:
    circuit = AbstractPhysicalCircuit(
        "disconnected_lane_reuse",
        signals=[AbstractSignal(1, "left"), AbstractSignal(2, "right")],
        nets=[
            AbstractNet(1, (1,), (), label="left carrier"),
            AbstractNet(2, (2,), (), label="right carrier"),
        ],
    )

    allocation = allocate_abstract_signals_dsat(
        circuit,
        {1: 1, 2: 2},
        signal_pool=_palette(1),
        reserved=set(),
        alias_roots={1: 1, 2: 2},
    )

    assert allocation[1] == allocation[2]


def test_abstract_lanes_sharing_one_carrier_must_use_distinct_signals() -> None:
    circuit = AbstractPhysicalCircuit(
        "shared_carrier_conflict",
        signals=[AbstractSignal(1, "left"), AbstractSignal(2, "right")],
        nets=[AbstractNet(1, (1, 2), (), label="shared carrier")],
    )

    allocation = allocate_abstract_signals_dsat(
        circuit,
        {1: 1},
        signal_pool=_palette(2),
        reserved=set(),
        alias_roots={1: 1, 2: 2},
    )

    assert allocation[1] != allocation[2]
