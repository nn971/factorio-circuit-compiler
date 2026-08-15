"""Target-independent logical causality records for state dependencies.

Causality is expressed only in logical occurrence coordinates.  Physical target latency belongs to
later timing/scheduling analysis and is deliberately optional at this boundary.  The transitional
``CausalityEdge`` subtype retains the old latency annotation for callers in ``state_timing`` while
the graph itself consumes the pure ``LogicalDependency`` base record.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.ir.state import StateRegister


class CausalityEdgeKind(StrEnum):
    """The semantic origin of a state-dependency edge."""

    ORDINARY_STATE_DEPENDENCY = "ordinary_state_dependency"
    EVENT_STATE_DEPENDENCY = "event_state_dependency"


@dataclass(frozen=True, slots=True)
class LogicalDependency:
    """One ordered state-recurrence dependency in logical occurrence coordinates."""

    source: StateRegister
    target: StateRegister
    kind: CausalityEdgeKind
    logical_displacement: int


@dataclass(frozen=True, slots=True)
class CausalityEdge(LogicalDependency):
    """Compatibility timing annotation for one logical dependency.

    New causality code should construct :class:`LogicalDependency` directly.  ``physical_latency``
    remains here while the physical timing solver is migrated to consume the logical graph plus its
    separate target-latency requirements.
    """

    physical_latency: int

    @property
    def logical(self) -> LogicalDependency:
        """Return the target-independent dependency represented by this timing edge."""

        return LogicalDependency(
            source=self.source,
            target=self.target,
            kind=self.kind,
            logical_displacement=self.logical_displacement,
        )


@dataclass(frozen=True, slots=True)
class CausalityGraph:
    """An immutable ordered multigraph of target-independent recurrence dependencies."""

    registers: tuple[StateRegister, ...]
    edges: tuple[LogicalDependency, ...]

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
    cycle using logical displacement alone.
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
