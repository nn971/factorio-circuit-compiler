"""Structural census for exact phase-delay transport in abstract physical circuits.

The ordinary physical census counts phase-delay entities.  This analysis goes one level deeper: it
reconstructs the delay-only graph from abstract nets, groups connected delay trunks, and records the
non-delay context at each trunk's roots and leaves.  It is diagnostic only and never participates in
lowering correctness or optimization decisions.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import asdict, dataclass

from factorio_circuit.ir import abstract_physical as abstract

_DELAY_DESCRIPTIONS = {
    "phase alignment delay": "scalar",
    "vector phase alignment delay": "vector",
}
_STATE_DESCRIPTION = re.compile(r"^(AccumulatorReg|FreezeReg) ([^:]+): (.+)$")
_NUMBERED_REGISTER_SUFFIX = re.compile(r"_\d+$")


@dataclass(frozen=True, slots=True)
class PhaseDelayComponent:
    """One connected component of exact scalar/vector delay combinators."""

    kind: str
    delay_entities: int
    roots: int
    leaves: int
    max_depth: int
    branch_points: int
    merge_points: int
    sources: tuple[str, ...]
    sinks: tuple[str, ...]

    @property
    def linear(self) -> bool:
        return (
            self.roots == 1
            and self.leaves == 1
            and self.branch_points == 0
            and self.merge_points == 0
        )


@dataclass(frozen=True, slots=True)
class PhaseDelayCensus:
    """Summary of residual exact phase transport and its graph context."""

    total_delays: int
    scalar_delays: int
    vector_delays: int
    components: int
    linear_components: int
    branching_components: int
    merging_components: int
    mixed_kind_components: int
    max_component_size: int
    max_depth: int
    component_size_histogram: tuple[tuple[str, int], ...]
    source_classes: tuple[tuple[str, int], ...]
    sink_classes: tuple[tuple[str, int], ...]
    component_details: tuple[PhaseDelayComponent, ...]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""

        return asdict(self)


def census_phase_delays(circuit: abstract.AbstractPhysicalCircuit) -> PhaseDelayCensus:
    """Reconstruct exact-delay trunks and summarize their source/sink context."""

    circuit.validate()
    entities = {entity.id: entity for entity in circuit.entities}
    nets = {net.id: net for net in circuit.nets}
    delay_kind = {
        entity.id: kind for entity in circuit.entities if (kind := _delay_kind(entity)) is not None
    }
    delay_ids = set(delay_kind)

    if not delay_ids:
        return PhaseDelayCensus(
            total_delays=0,
            scalar_delays=0,
            vector_delays=0,
            components=0,
            linear_components=0,
            branching_components=0,
            merging_components=0,
            mixed_kind_components=0,
            max_component_size=0,
            max_depth=0,
            component_size_histogram=(),
            source_classes=(),
            sink_classes=(),
            component_details=(),
        )

    input_ports = {port.endpoint: port.name for port in circuit.inputs}
    output_ports = {port.endpoint: port.name for port in circuit.outputs}

    output_nets_by_entity: dict[int, list[int]] = {}
    for net in circuit.nets:
        for endpoint in net.endpoints:
            if endpoint.connector is abstract.Connector.OUTPUT:
                output_nets_by_entity.setdefault(endpoint.entity, []).append(net.id)

    input_net: dict[int, int] = {}
    output_net: dict[int, int] = {}
    for entity_id in delay_ids:
        entity = entities[entity_id]
        if not isinstance(entity, abstract.ArithmeticCombinator):  # pragma: no cover - classifier
            raise AssertionError("phase delay must be an arithmetic combinator")
        if len(entity.left.nets) != 1:  # pragma: no cover - lowering invariant
            raise ValueError(f"phase delay {entity_id} does not have exactly one source net")
        outputs = output_nets_by_entity.get(entity_id, [])
        if len(outputs) != 1:  # pragma: no cover - lowering invariant
            raise ValueError(f"phase delay {entity_id} does not have exactly one output net")
        input_net[entity_id] = entity.left.nets[0]
        output_net[entity_id] = outputs[0]

    delays_by_input_net: dict[int, list[int]] = {}
    for entity_id, net_id in input_net.items():
        delays_by_input_net.setdefault(net_id, []).append(entity_id)

    successors: dict[int, set[int]] = {entity_id: set() for entity_id in delay_ids}
    predecessors: dict[int, set[int]] = {entity_id: set() for entity_id in delay_ids}
    for entity_id in delay_ids:
        for successor in delays_by_input_net.get(output_net[entity_id], ()):
            if successor == entity_id:
                continue
            successors[entity_id].add(successor)
            predecessors[successor].add(entity_id)

    components: list[PhaseDelayComponent] = []
    unseen = set(delay_ids)
    while unseen:
        start = min(unseen)
        member_ids: set[int] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in member_ids:
                continue
            member_ids.add(current)
            stack.extend(successors[current] - member_ids)
            stack.extend(predecessors[current] - member_ids)
        unseen -= member_ids

        roots = sorted(
            entity_id for entity_id in member_ids if not (predecessors[entity_id] & member_ids)
        )
        leaves = sorted(
            entity_id for entity_id in member_ids if not (successors[entity_id] & member_ids)
        )
        depth = _component_depth(member_ids, successors, predecessors)
        kinds = {delay_kind[entity_id] for entity_id in member_ids}
        sources = sorted(
            {
                label
                for entity_id in roots
                for label in _source_labels(
                    nets[input_net[entity_id]],
                    entities,
                    delay_ids,
                    input_ports,
                )
            }
        )
        sinks = sorted(
            {
                label
                for entity_id in leaves
                for label in _sink_labels(
                    nets[output_net[entity_id]],
                    entities,
                    delay_ids,
                    output_ports,
                )
            }
        )
        components.append(
            PhaseDelayComponent(
                kind=next(iter(kinds)) if len(kinds) == 1 else "+".join(sorted(kinds)),
                delay_entities=len(member_ids),
                roots=len(roots),
                leaves=len(leaves),
                max_depth=depth,
                branch_points=sum(
                    len(successors[entity_id] & member_ids) > 1 for entity_id in member_ids
                ),
                merge_points=sum(
                    len(predecessors[entity_id] & member_ids) > 1 for entity_id in member_ids
                ),
                sources=tuple(sources or ("unknown",)),
                sinks=tuple(sinks or ("unknown",)),
            )
        )

    components.sort(
        key=lambda item: (
            -item.delay_entities,
            -item.max_depth,
            item.kind,
            item.sources,
            item.sinks,
        )
    )

    size_histogram: Counter[str] = Counter()
    source_classes: Counter[str] = Counter()
    sink_classes: Counter[str] = Counter()
    for component in components:
        size_histogram[_size_bucket(component.delay_entities)] += 1
        source_classes[_context_class(component.sources)] += component.delay_entities
        sink_classes[_context_class(component.sinks)] += component.delay_entities

    scalar_delays = sum(kind == "scalar" for kind in delay_kind.values())
    vector_delays = sum(kind == "vector" for kind in delay_kind.values())
    return PhaseDelayCensus(
        total_delays=len(delay_ids),
        scalar_delays=scalar_delays,
        vector_delays=vector_delays,
        components=len(components),
        linear_components=sum(component.linear for component in components),
        branching_components=sum(component.branch_points > 0 for component in components),
        merging_components=sum(component.merge_points > 0 for component in components),
        mixed_kind_components=sum("+" in component.kind for component in components),
        max_component_size=max(component.delay_entities for component in components),
        max_depth=max(component.max_depth for component in components),
        component_size_histogram=tuple(
            (bucket, size_histogram[bucket])
            for bucket in ("1", "2", "3-4", "5-8", "9-16", "17-32", "33-64", "65+")
            if size_histogram[bucket]
        ),
        source_classes=_sorted_counts(source_classes),
        sink_classes=_sorted_counts(sink_classes),
        component_details=tuple(components),
    )


def format_phase_delay_census(census: PhaseDelayCensus, *, top: int = 20) -> str:
    """Render a compact human-readable residual-delay report."""

    lines = [
        "phase delay deep census",
        (
            "  delays: "
            f"total={census.total_delays}; scalar={census.scalar_delays}; "
            f"vector={census.vector_delays}"
        ),
        (
            "  components: "
            f"total={census.components}; linear={census.linear_components}; "
            f"branching={census.branching_components}; merging={census.merging_components}; "
            f"mixed_kind={census.mixed_kind_components}"
        ),
        (f"  maxima: component_size={census.max_component_size}; depth={census.max_depth}"),
    ]
    _append_counts(lines, "component size histogram", census.component_size_histogram)
    _append_counts(lines, "delay-weighted source classes", census.source_classes)
    _append_counts(lines, "delay-weighted sink classes", census.sink_classes)

    lines.append(f"  largest components (top {min(top, census.components)}):")
    if not census.component_details:
        lines.append("    (none)")
        return "\n".join(lines)
    for index, component in enumerate(census.component_details[:top], start=1):
        source = _short_context(component.sources)
        sink = _short_context(component.sinks)
        lines.append(
            f"    {index:>2}. {component.kind:<13} size={component.delay_entities:<3} "
            f"depth={component.max_depth:<3} roots={component.roots:<2} "
            f"leaves={component.leaves:<2} "
            f"branch={component.branch_points:<2} merge={component.merge_points:<2}"
        )
        lines.append(f"        {source} -> {sink}")
    return "\n".join(lines)


def _delay_kind(entity: abstract.AbstractEntity) -> str | None:
    if not isinstance(entity, abstract.ArithmeticCombinator):
        return None
    return _DELAY_DESCRIPTIONS.get(entity.description or "")


def _component_depth(
    members: set[int],
    successors: dict[int, set[int]],
    predecessors: dict[int, set[int]],
) -> int:
    """Return longest delay-node path length, or zero if a malformed cycle is encountered."""

    indegree = {entity_id: len(predecessors[entity_id] & members) for entity_id in members}
    queue = deque(sorted(entity_id for entity_id, degree in indegree.items() if degree == 0))
    depths = {entity_id: 1 for entity_id in queue}
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for successor in sorted(successors[current] & members):
            depths[successor] = max(depths.get(successor, 1), depths[current] + 1)
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if visited != len(members):
        return 0
    return max(depths.values(), default=0)


def _source_labels(
    net: abstract.AbstractNet,
    entities: dict[int, abstract.AbstractEntity],
    delay_ids: set[int],
    input_ports: dict[abstract.Endpoint, str],
) -> tuple[str, ...]:
    labels: set[str] = set()
    for endpoint in net.endpoints:
        input_name = input_ports.get(endpoint)
        if input_name is not None:
            labels.add(f"input:{input_name}")
        if endpoint.entity in delay_ids:
            continue
        entity = entities[endpoint.entity]
        if endpoint.connector is abstract.Connector.OUTPUT or (
            endpoint.connector is abstract.Connector.SINGLE
            and isinstance(entity, abstract.ConstantCombinator)
            and input_name is None
        ):
            labels.add(_entity_context(entity))
    if not labels and net.label:
        labels.add(f"net:{net.label}")
    return tuple(sorted(labels))


def _sink_labels(
    net: abstract.AbstractNet,
    entities: dict[int, abstract.AbstractEntity],
    delay_ids: set[int],
    output_ports: dict[abstract.Endpoint, str],
) -> tuple[str, ...]:
    labels: set[str] = set()
    for endpoint in net.endpoints:
        output_name = output_ports.get(endpoint)
        if output_name is not None:
            labels.add(f"output:{output_name}")
        if endpoint.entity in delay_ids:
            continue
        if endpoint.connector is abstract.Connector.INPUT:
            labels.add(_entity_context(entities[endpoint.entity]))
    if not labels and net.label:
        labels.add(f"net:{net.label}")
    return tuple(sorted(labels))


def _entity_context(entity: abstract.AbstractEntity) -> str:
    description = entity.description or ""
    lower = description.lower()
    state = _STATE_DESCRIPTION.fullmatch(description)
    if state is not None:
        register_kind, register_name, action = state.groups()
        family = _NUMBERED_REGISTER_SUFFIX.sub("_*", register_name)
        return f"state:{register_kind}.{family}:{action}"
    if description.startswith("INPUT "):
        return "input-marker"
    if description.startswith("OUTPUT "):
        return "output-marker"
    if description.startswith("Level HOLD:"):
        return f"output-hold:{description.removeprefix('Level HOLD:').strip()}"
    if "startup" in lower or "clock" in lower:
        return f"clock/startup:{description or type(entity).__name__}"
    if isinstance(entity, abstract.ConstantCombinator):
        return f"constant:{description or 'constant'}"
    if isinstance(entity, abstract.ArithmeticCombinator):
        return f"computation:{description or f'arithmetic {entity.operation}'}"
    if isinstance(entity, abstract.DeciderCombinator):
        return f"computation:{description or f'decider {entity.comparator}'}"
    return f"computation:{description or type(entity).__name__}"


def _context_class(labels: tuple[str, ...]) -> str:
    classes = sorted({_single_context_class(label) for label in labels})
    if not classes:
        return "unknown"
    if len(classes) == 1:
        return classes[0]
    return "mixed[" + "+".join(classes) + "]"


def _single_context_class(label: str) -> str:
    if label.startswith("input:") or label == "input-marker":
        return "external-input"
    if label.startswith("state:"):
        return "state"
    if label.startswith("clock/startup:"):
        return "clock/startup"
    if label.startswith("constant:"):
        return "constant"
    if label.startswith("output-hold:"):
        return "output-hold"
    if label.startswith("output:") or label == "output-marker":
        return "output"
    if label.startswith("computation:"):
        return "computation"
    if label.startswith("net:"):
        return "unclassified-net"
    return "unknown"


def _size_bucket(size: int) -> str:
    if size <= 1:
        return "1"
    if size == 2:
        return "2"
    if size <= 4:
        return "3-4"
    if size <= 8:
        return "5-8"
    if size <= 16:
        return "9-16"
    if size <= 32:
        return "17-32"
    if size <= 64:
        return "33-64"
    return "65+"


def _sorted_counts(counts: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _append_counts(lines: list[str], heading: str, counts: tuple[tuple[str, int], ...]) -> None:
    lines.append(f"  {heading}:")
    if not counts:
        lines.append("    (none)")
        return
    width = max(len(name) for name, _count in counts)
    for name, count in counts:
        lines.append(f"    {name:<{width}}  {count}")


def _short_context(labels: tuple[str, ...], *, limit: int = 120) -> str:
    text = ", ".join(labels)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


__all__ = [
    "PhaseDelayCensus",
    "PhaseDelayComponent",
    "census_phase_delays",
    "format_phase_delay_census",
]
