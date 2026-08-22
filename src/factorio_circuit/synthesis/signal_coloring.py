"""Deterministic graph coloring for concrete Factorio signal allocation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import SignalId


def color_interference_graph_dsat(
    adjacency: Mapping[int, set[int]],
    palette: tuple[SignalId, ...],
) -> dict[int, SignalId]:
    """Color an interference graph with deterministic DSATUR ordering."""

    uncolored = set(adjacency)
    allocation: dict[int, SignalId] = {}
    while uncolored:

        def priority(vertex: int) -> tuple[int, int, int]:
            neighbor_colors = {
                allocation[neighbor] for neighbor in adjacency[vertex] if neighbor in allocation
            }
            return (-len(neighbor_colors), -len(adjacency[vertex]), vertex)

        vertex = min(uncolored, key=priority)
        forbidden = {
            allocation[neighbor] for neighbor in adjacency[vertex] if neighbor in allocation
        }
        concrete = next((signal for signal in palette if signal not in forbidden), None)
        if concrete is None:
            raise ValueError(
                "DSATUR signal coloring exhausted its palette at "
                f"vertex={vertex}; palette={len(palette)}; degree={len(adjacency[vertex])}; "
                f"saturation={len(forbidden)}; colors_used={len(set(allocation.values()))}"
            )
        allocation[vertex] = concrete
        uncolored.remove(vertex)
    return allocation


def allocate_abstract_signals_dsat(
    circuit: abstract.AbstractPhysicalCircuit,
    net_groups: Mapping[int, int],
    *,
    signal_pool: tuple[SignalId, ...],
    reserved: set[SignalId],
    alias_roots: Mapping[int, int],
) -> dict[int, SignalId]:
    """Allocate concrete signal identities for one abstract physical circuit."""

    available = tuple(signal for signal in signal_pool if signal not in reserved)
    if not available and circuit.signals:
        raise ValueError(
            "physical synthesis has no concrete scratch signals available for allocation; "
            f"palette={len(signal_pool)}; reserved={len(set(signal_pool) & reserved)}"
        )

    members_by_root: dict[int, list[int]] = defaultdict(list)
    for signal in circuit.signals:
        members_by_root[alias_roots[signal.id]].append(signal.id)

    signal_groups: dict[int, set[int]] = {root: set() for root in members_by_root}
    group_members: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for net in circuit.nets:
        if net.carries_dynamic_vector and net.signals:
            raise ValueError(
                f"runtime-open vector net {net.id} cannot carry compiler-allocated abstract lanes"
            )
        group = net_groups[net.id]
        for signal_id in net.signals:
            root = alias_roots[signal_id]
            signal_groups[root].add(group)
            group_members[group][root].add(signal_id)

    for group, by_root in group_members.items():
        collapsed = [sorted(members) for members in by_root.values() if len(members) > 1]
        if collapsed:
            raise ValueError(
                "signal-alias constraint would collapse distinct lanes on synthesized "
                f"electrical group {group}: {collapsed}"
            )

    adjacency: dict[int, set[int]] = {root: set() for root in members_by_root}
    for conflict in circuit.signal_conflicts:
        left = alias_roots[conflict.left]
        right = alias_roots[conflict.right]
        if left == right:
            raise ValueError("signal alias class contains a pair that is also required to conflict")
        adjacency[left].add(right)
        adjacency[right].add(left)

    roots = sorted(adjacency)
    for index, left in enumerate(roots):
        for right in roots[index + 1 :]:
            if signal_groups[left] & signal_groups[right]:
                adjacency[left].add(right)
                adjacency[right].add(left)

    by_id = {signal.id: signal for signal in circuit.signals}
    for root in roots:
        for signal_id in members_by_root[root]:
            domain = by_id[signal_id].domain
            if domain not in {abstract.SignalDomain.ANY, abstract.SignalDomain.VIRTUAL}:
                raise ValueError(
                    f"baseline physical synthesis cannot allocate {domain.value} signals yet"
                )

    try:
        root_allocation = color_interference_graph_dsat(adjacency, available)
    except ValueError as exc:
        max_degree = max((len(neighbors) for neighbors in adjacency.values()), default=0)
        largest_group_clique = max((len(members) for members in group_members.values()), default=0)
        raise ValueError(
            "physical synthesis exhausted the concrete scratch-signal pool while coloring the "
            "abstract signal-interference graph; "
            f"palette={len(signal_pool)}; "
            f"reserved_from_palette={len(set(signal_pool) & reserved)}; "
            f"available={len(available)}; vertices={len(roots)}; max_degree={max_degree}; "
            f"largest_group_clique={largest_group_clique}; detail=({exc})"
        ) from exc

    return {signal.id: root_allocation[alias_roots[signal.id]] for signal in circuit.signals}


__all__ = ["allocate_abstract_signals_dsat", "color_interference_graph_dsat"]
