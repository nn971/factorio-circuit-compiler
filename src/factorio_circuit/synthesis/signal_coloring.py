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


def _signal_matches_domain(signal: SignalId, domain: abstract.SignalDomain) -> bool:
    if domain is abstract.SignalDomain.ANY:
        return True
    return signal.kind == domain.value


def _color_with_fixed_roots(
    adjacency: Mapping[int, set[int]],
    palette: tuple[SignalId, ...],
    fixed: Mapping[int, SignalId],
) -> dict[int, SignalId]:
    allocation = dict(fixed)
    for root, fixed_concrete in fixed.items():
        for neighbor in adjacency[root]:
            if fixed.get(neighbor) == fixed_concrete:
                raise ValueError(
                    "fixed signal assignments give interfering abstract lanes the same concrete "
                    f"Factorio signal {fixed_concrete!r}"
                )

    uncolored = set(adjacency) - allocation.keys()
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
    fixed_allocations: Mapping[int, SignalId] | None = None,
) -> dict[int, SignalId]:
    """Allocate concrete signal identities for one abstract physical circuit.

    ``fixed_allocations`` precolors selected abstract lanes with exact Factorio identities. The
    assignment is applied to the complete signal-alias class and participates in the same
    interference graph as ordinary DSATUR allocation. This is useful for typed physical interfaces
    whose ABI names a concrete scalar signal while retaining the compiler's normal reuse rules for
    every other lane.
    """

    available = tuple(signal for signal in signal_pool if signal not in reserved)

    members_by_root: dict[int, list[int]] = defaultdict(list)
    for signal in circuit.signals:
        members_by_root[alias_roots[signal.id]].append(signal.id)

    fixed_roots: dict[int, SignalId] = {}
    for signal_id, concrete in (fixed_allocations or {}).items():
        if signal_id not in alias_roots:
            raise ValueError(f"fixed allocation references unknown abstract signal {signal_id}")
        root = alias_roots[signal_id]
        previous = fixed_roots.setdefault(root, concrete)
        if previous != concrete:
            raise ValueError(
                f"signal alias class {root} has conflicting fixed concrete assignments"
            )

    if not available and len(fixed_roots) < len(members_by_root):
        raise ValueError(
            "physical synthesis has no concrete scratch signals available for allocation; "
            f"palette={len(signal_pool)}; reserved={len(set(signal_pool) & reserved)}"
        )

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
        fixed = fixed_roots.get(root)
        for signal_id in members_by_root[root]:
            domain = by_id[signal_id].domain
            if fixed is not None:
                if not _signal_matches_domain(fixed, domain):
                    raise ValueError(
                        f"fixed Factorio signal {fixed!r} violates {domain.value} domain for "
                        f"abstract lane {signal_id}"
                    )
                continue
            if domain not in {abstract.SignalDomain.ANY, abstract.SignalDomain.VIRTUAL}:
                raise ValueError(
                    f"baseline physical synthesis cannot allocate {domain.value} signals yet"
                )

    try:
        root_allocation = (
            _color_with_fixed_roots(adjacency, available, fixed_roots)
            if fixed_roots
            else color_interference_graph_dsat(adjacency, available)
        )
    except ValueError as exc:
        max_degree = max((len(neighbors) for neighbors in adjacency.values()), default=0)
        largest_group_clique = max((len(members) for members in group_members.values()), default=0)
        if not fixed_roots:
            raise ValueError(
                "physical synthesis exhausted the concrete scratch-signal pool while coloring the "
                "abstract signal-interference graph; "
                f"palette={len(signal_pool)}; "
                f"reserved_from_palette={len(set(signal_pool) & reserved)}; "
                f"available={len(available)}; vertices={len(roots)}; max_degree={max_degree}; "
                f"largest_group_clique={largest_group_clique}; detail=({exc})"
            ) from exc
        raise ValueError(
            "physical synthesis exhausted or contradicted the concrete scratch-signal allocation "
            "while honoring fixed abstract signal identities; "
            f"palette={len(signal_pool)}; "
            f"reserved_from_palette={len(set(signal_pool) & reserved)}; "
            f"available={len(available)}; vertices={len(roots)}; max_degree={max_degree}; "
            f"largest_group_clique={largest_group_clique}; fixed_roots={len(fixed_roots)}; "
            f"detail=({exc})"
        ) from exc

    return {signal.id: root_allocation[alias_roots[signal.id]] for signal in circuit.signals}


__all__ = ["allocate_abstract_signals_dsat", "color_interference_graph_dsat"]
