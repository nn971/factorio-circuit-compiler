"""Internal weighted causality records for ordinary state dependencies.

This module intentionally models only state-register recurrence edges.  External values, clocks,
events, bridges, output policies, and expression trees remain outside this Phase 2 graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.ir.state import StateRegister


class CausalityEdgeKind(StrEnum):
    """The currently supported state-dependency edge kind."""

    ORDINARY_STATE_DEPENDENCY = "ordinary_state_dependency"


@dataclass(frozen=True, slots=True)
class CausalityEdge:
    """One ordered state-recurrence dependency.

    Graph edge tuples preserve requirement order and duplicate edges.  ``physical_latency`` is
    carried for later timing extraction, but is deliberately ignored by the causality predicate.
    """

    source: StateRegister
    target: StateRegister
    kind: CausalityEdgeKind
    logical_displacement: int
    physical_latency: int


@dataclass(frozen=True, slots=True)
class CausalityGraph:
    """An immutable ordered multigraph of ordinary state recurrence dependencies."""

    registers: tuple[StateRegister, ...]
    edges: tuple[CausalityEdge, ...]

    def __post_init__(self) -> None:
        register_set = set(self.registers)
        if len(register_set) != len(self.registers):
            raise ValueError("causality graph registers must be unique")
        for edge in self.edges:
            if edge.source not in register_set or edge.target not in register_set:
                raise ValueError("causality edge endpoints must be listed graph registers")


def has_nonpositive_cycle(graph: CausalityGraph) -> bool:
    """Return whether the graph contains a directed cycle of total displacement ``<= 0``.

    A simple cycle has at most ``N`` edges for ``N`` graph registers.  Transforming an edge weight
    ``d`` to ``(N + 1) * d - 1`` makes every non-positive-displacement simple cycle negative while
    every positive-displacement simple cycle remains positive.  Bellman-Ford then detects such a
    cycle without considering physical latency.
    """

    if not graph.registers or not graph.edges:
        return False

    index = {register: position for position, register in enumerate(graph.registers)}
    count = len(graph.registers)
    transformed = tuple(
        (
            index[edge.source],
            index[edge.target],
            (count + 1) * edge.logical_displacement - 1,
        )
        for edge in graph.edges
    )

    distances = [0] * count
    for iteration in range(count):
        changed = False
        for source, target, weight in transformed:
            candidate = distances[source] + weight
            if candidate < distances[target]:
                distances[target] = candidate
                changed = True
        if not changed:
            return False
        if iteration == count - 1:
            return True
    return False
