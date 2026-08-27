"""Generic multilevel coarsening for physical implementation placement.

The hierarchy is derived only from implementation entities and their logical red/green electrical
nets. Routed relay combinators and current geometric distances are deliberately absent: a failproof
seed may contain a very large routing scaffold whose shape should not become a placement prior.

Coarsening uses deterministic heavy-edge matching. A k-terminal hypernet contributes 1/(k-1) pair
affinity between every pair of macros it touches, so one large bus does not dominate a dedicated
small net merely because it contains quadratically many terminal pairs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from factorio_circuit.ir.physical import Connector, PhysicalCircuit, WireColor

_EndpointKey = tuple[int, Connector, WireColor]


class _DisjointSet:
    def __init__(self) -> None:
        self._parent: dict[_EndpointKey, _EndpointKey] = {}

    def add(self, item: _EndpointKey) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: _EndpointKey) -> _EndpointKey:
        self.add(item)
        parent = self._parent[item]
        if parent != item:
            self._parent[item] = self.find(parent)
        return self._parent[item]

    def union(self, left: _EndpointKey, right: _EndpointKey) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parent[max(left_root, right_root)] = min(left_root, right_root)


@dataclass(frozen=True, slots=True)
class ImplementationHyperedge:
    """One logical electrical net projected to its implementation entity members."""

    members: tuple[int, ...]
    color: WireColor


@dataclass(frozen=True, slots=True)
class PlacementMacro:
    """A deterministic set of implementation entities treated as one coarse placement object."""

    members: tuple[int, ...]
    fixed: bool = False


@dataclass(frozen=True, slots=True)
class CoarseningLevel:
    """One partition of implementation entities into placement macros."""

    macros: tuple[PlacementMacro, ...]


@dataclass(frozen=True, slots=True)
class MultilevelHierarchy:
    """Relay-blind logical hypergraph plus progressively coarser macro partitions."""

    hyperedges: tuple[ImplementationHyperedge, ...]
    levels: tuple[CoarseningLevel, ...]


def implementation_hyperedges(circuit: PhysicalCircuit) -> tuple[ImplementationHyperedge, ...]:
    """Reconstruct logical red/green hypernets without consulting routed relay geometry."""

    disjoint = _DisjointSet()
    endpoints: set[_EndpointKey] = set()
    for connection in circuit.connections:
        left = (connection.source.entity, connection.source.connector, connection.color)
        right = (connection.target.entity, connection.target.connector, connection.color)
        endpoints.update((left, right))
        disjoint.union(left, right)

    members_by_root: dict[_EndpointKey, set[int]] = defaultdict(set)
    for endpoint in endpoints:
        members_by_root[disjoint.find(endpoint)].add(endpoint[0])

    edges = [
        ImplementationHyperedge(tuple(sorted(members)), root[2])
        for root, members in members_by_root.items()
        if len(members) >= 2
    ]
    return tuple(sorted(edges, key=lambda edge: (edge.color.value, edge.members)))


def macro_pair_affinities(
    level: CoarseningLevel,
    hyperedges: tuple[ImplementationHyperedge, ...],
) -> dict[tuple[int, int], float]:
    """Return normalized logical affinity between macros in one level."""

    owner = {
        entity_id: macro_index
        for macro_index, macro in enumerate(level.macros)
        for entity_id in macro.members
    }
    affinities: dict[tuple[int, int], float] = defaultdict(float)
    for edge in hyperedges:
        touched = tuple(sorted({owner[entity_id] for entity_id in edge.members}))
        if len(touched) <= 1:
            continue
        contribution = 1.0 / (len(touched) - 1)
        for left, right in combinations(touched, 2):
            affinities[(left, right)] += contribution
    return dict(affinities)


def coarsen_level(
    level: CoarseningLevel,
    hyperedges: tuple[ImplementationHyperedge, ...],
) -> CoarseningLevel:
    """Greedily merge the strongest disjoint macro pairs with deterministic tie-breaking."""

    affinities = macro_pair_affinities(level, hyperedges)
    candidates = sorted(
        affinities,
        key=lambda pair: (
            -affinities[pair],
            level.macros[pair[0]].members,
            level.macros[pair[1]].members,
        ),
    )
    matched: set[int] = set()
    merged: list[PlacementMacro] = []
    for left_index, right_index in candidates:
        if left_index in matched or right_index in matched:
            continue
        left = level.macros[left_index]
        right = level.macros[right_index]
        if left.fixed or right.fixed:
            continue
        merged.append(PlacementMacro(tuple(sorted((*left.members, *right.members)))))
        matched.update((left_index, right_index))

    merged.extend(macro for index, macro in enumerate(level.macros) if index not in matched)
    return CoarseningLevel(tuple(sorted(merged, key=lambda macro: macro.members)))


def build_multilevel_hierarchy(
    circuit: PhysicalCircuit,
    *,
    fixed_entities: frozenset[int] = frozenset(),
    target_macros: int = 32,
    max_levels: int = 16,
) -> MultilevelHierarchy:
    """Build deterministic heavy-edge levels to the target or a matching fixed point."""

    if target_macros <= 0:
        raise ValueError("target_macros must be positive")
    if max_levels <= 0:
        raise ValueError("max_levels must be positive")
    entity_ids = {entity.id for entity in circuit.entities}
    unknown_fixed = fixed_entities - entity_ids
    if unknown_fixed:
        raise ValueError(f"fixed_entities contains unknown entity ids: {sorted(unknown_fixed)}")

    hyperedges = implementation_hyperedges(circuit)
    initial = CoarseningLevel(
        tuple(
            PlacementMacro((entity_id,), fixed=entity_id in fixed_entities)
            for entity_id in sorted(entity_ids)
        )
    )
    levels = [initial]
    while len(levels[-1].macros) > target_macros and len(levels) < max_levels:
        candidate = coarsen_level(levels[-1], hyperedges)
        if len(candidate.macros) == len(levels[-1].macros):
            break
        levels.append(candidate)
    return MultilevelHierarchy(hyperedges, tuple(levels))
