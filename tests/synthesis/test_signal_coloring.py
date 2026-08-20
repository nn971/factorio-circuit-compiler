import pytest

from factorio_circuit.ir.physical import SignalId
from factorio_circuit.synthesis.signal_coloring import color_interference_graph_dsat


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
