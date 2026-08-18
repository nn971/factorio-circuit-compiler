"""Experimental projection from eager phase-delay chains to synthetic temporal holds.

This module does not rewrite the canonical AbstractPhysicalCircuit.  It analyzes the current lowering
only as a baseline and asks a narrower question: how many maximal delay components carry one logical
Level token forward through a period?  If a component's maximum delay depth is strictly smaller than
the clock inactive interval, one synthetic capture/hold at the component root can replace all delay
combinators in that component.

The result is a conservative projection.  A future phase-free temporal optimizer may share one hold
across several components or prove that no hold is needed because the root value is naturally stable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from factorio_circuit.ir.abstract_physical import (
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    Endpoint,
)

_SCALAR_DELAY = "phase alignment delay"
_VECTOR_DELAY = "vector phase alignment delay"


@dataclass(frozen=True, slots=True)
class DelayComponent:
    """One rooted DAG of eager one-tick delays carrying the same token forward."""

    root_entity: int
    kind: str
    entities: tuple[int, ...]
    max_depth: int
    root_input_net: int

    @property
    def delay_count(self) -> int:
        return len(self.entities)


@dataclass(frozen=True, slots=True)
class DelayReuseProjection:
    """Conservative one-hold-per-delay-component projection."""

    period: int
    scalar_delays: int
    vector_delays: int
    components: tuple[DelayComponent, ...]
    eligible_components: tuple[DelayComponent, ...]
    ineligible_components: tuple[DelayComponent, ...]

    @property
    def delay_count(self) -> int:
        return self.scalar_delays + self.vector_delays

    @property
    def projected_holds(self) -> int:
        return len(self.eligible_components)

    @property
    def removable_delays(self) -> int:
        return sum(component.delay_count for component in self.eligible_components)

    @property
    def remaining_delays(self) -> int:
        return self.delay_count - self.removable_delays

    @property
    def reduction_fraction(self) -> float:
        return 0.0 if self.delay_count == 0 else self.removable_delays / self.delay_count

    def summary(self) -> str:
        sizes = Counter(_bucket(component.delay_count) for component in self.components)
        depths = Counter(_bucket(component.max_depth) for component in self.components)
        kinds = Counter(component.kind for component in self.components)
        lines = [
            "experimental delay-reuse projection",
            (
                f"  period={self.period}; eager_delays={self.delay_count}; "
                f"scalar={self.scalar_delays}; vector={self.vector_delays}"
            ),
            (
                f"  delay_components={len(self.components)}; "
                f"eligible={len(self.eligible_components)}; "
                f"ineligible={len(self.ineligible_components)}"
            ),
            (
                f"  projected_temporal_holds={self.projected_holds}; "
                f"removable_delays={self.removable_delays}; "
                f"remaining_delays={self.remaining_delays}; "
                f"delay_reduction={100.0 * self.reduction_fraction:.2f}%"
            ),
            "  components by kind:",
        ]
        for label, count in sorted(kinds.items()):
            lines.append(f"    {label}: {count}")
        lines.append("  component size histogram:")
        for label in _BUCKETS:
            if sizes[label]:
                lines.append(f"    {label}: {sizes[label]}")
        lines.append("  component max-depth histogram:")
        for label in _BUCKETS:
            if depths[label]:
                lines.append(f"    {label}: {depths[label]}")
        if self.components:
            longest = sorted(
                self.components,
                key=lambda item: (-item.max_depth, -item.delay_count, item.root_entity),
            )[:10]
            lines.append("  longest components (root, kind, delays, depth):")
            for component in longest:
                lines.append(
                    f"    {component.root_entity}, {component.kind}, "
                    f"{component.delay_count}, {component.max_depth}"
                )
        return "\n".join(lines)


_BUCKETS = ("1", "2", "3-4", "5-8", "9-16", "17-32", "33-64", "65+")


def _bucket(value: int) -> str:
    if value <= 1:
        return "1"
    if value == 2:
        return "2"
    if value <= 4:
        return "3-4"
    if value <= 8:
        return "5-8"
    if value <= 16:
        return "9-16"
    if value <= 32:
        return "17-32"
    if value <= 64:
        return "33-64"
    return "65+"


def _delay_kind(entity: object) -> str | None:
    if not isinstance(entity, ArithmeticCombinator):
        return None
    if entity.description == _SCALAR_DELAY:
        return "scalar"
    if entity.description == _VECTOR_DELAY:
        return "vector"
    return None


def project_delay_reuse(
    circuit: AbstractPhysicalCircuit,
    *,
    period: int,
) -> DelayReuseProjection:
    """Project maximal eager-delay components to one synthetic hold each.

    A component is eligible exactly when its longest one-tick delay path is shorter than ``period``.
    This is the conservative fixed-period condition needed for a token captured once per occurrence
    to remain available through every use represented by the component without being overwritten by
    the next occurrence.
    """

    circuit.validate()
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("period must be a positive integer")

    entities = {entity.id: entity for entity in circuit.entities}
    delays = {
        entity_id: kind
        for entity_id, entity in entities.items()
        if (kind := _delay_kind(entity)) is not None
    }

    input_net: dict[int, int] = {}
    output_net: dict[int, int] = {}
    net_producers: dict[int, set[int]] = {}
    for net in circuit.nets:
        producers: set[int] = set()
        for endpoint in net.endpoints:
            if endpoint.connector is Connector.INPUT and endpoint.entity in delays:
                previous = input_net.setdefault(endpoint.entity, net.id)
                if previous != net.id:
                    raise ValueError("phase-delay entity has multiple input nets")
            if endpoint.connector is Connector.OUTPUT:
                producers.add(endpoint.entity)
                if endpoint.entity in delays:
                    previous = output_net.setdefault(endpoint.entity, net.id)
                    if previous != net.id:
                        raise ValueError("phase-delay entity has multiple output nets")
        net_producers[net.id] = producers

    if set(input_net) != set(delays) or set(output_net) != set(delays):
        missing = sorted(set(delays) - set(input_net) | set(delays) - set(output_net))
        raise ValueError(f"phase-delay entities missing canonical connectors: {missing[:8]}")

    predecessor: dict[int, int | None] = {}
    children: dict[int, list[int]] = {entity_id: [] for entity_id in delays}
    for entity_id, kind in delays.items():
        candidates = [
            producer
            for producer in net_producers[input_net[entity_id]]
            if producer in delays and delays[producer] == kind
        ]
        if len(candidates) > 1:
            raise ValueError("phase-delay input has multiple delay producers")
        parent = candidates[0] if candidates else None
        predecessor[entity_id] = parent
        if parent is not None:
            children[parent].append(entity_id)

    roots = sorted(entity_id for entity_id, parent in predecessor.items() if parent is None)
    components: list[DelayComponent] = []
    visited: set[int] = set()
    for root in roots:
        kind = delays[root]
        stack = [(root, 1)]
        members: list[int] = []
        max_depth = 0
        while stack:
            current, depth = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            members.append(current)
            max_depth = max(max_depth, depth)
            stack.extend((child, depth + 1) for child in children[current])
        components.append(
            DelayComponent(
                root_entity=root,
                kind=kind,
                entities=tuple(sorted(members)),
                max_depth=max_depth,
                root_input_net=input_net[root],
            )
        )

    if visited != set(delays):
        # A remaining component would be a pure delay cycle, which should be impossible for the
        # current eager forward-only lowering and cannot be replaced by a simple occurrence hold.
        unresolved = sorted(set(delays) - visited)
        raise ValueError(f"phase-delay graph contains a cycle or unreachable component: {unresolved[:8]}")

    ordered = tuple(sorted(components, key=lambda item: item.root_entity))
    eligible = tuple(component for component in ordered if component.max_depth < period)
    ineligible = tuple(component for component in ordered if component.max_depth >= period)
    return DelayReuseProjection(
        period=period,
        scalar_delays=sum(kind == "scalar" for kind in delays.values()),
        vector_delays=sum(kind == "vector" for kind in delays.values()),
        components=ordered,
        eligible_components=eligible,
        ineligible_components=ineligible,
    )
