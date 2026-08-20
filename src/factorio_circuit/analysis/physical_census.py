"""Structural census for abstract physical Factorio circuits.

The census is diagnostic only. It reports what lowering actually emitted before physical synthesis
chooses signal identities, wire colors, net coalescing, placement, or routing. Generated-description
classification is intentionally kept here rather than used for optimizer correctness decisions.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass

from factorio_circuit.ir import abstract_physical as abstract

_STATE_DESCRIPTION = re.compile(r"^(AccumulatorReg|FreezeReg) ([^:]+): (.+)$")
_NUMBERED_REGISTER_SUFFIX = re.compile(r"_\d+$")


@dataclass(frozen=True, slots=True)
class AbstractPhysicalCensus:
    """Compact structural summary of one ``AbstractPhysicalCircuit``."""

    implementation_entities: int
    annotation_entities: int
    entity_kinds: tuple[tuple[str, int], ...]
    lowering_roles: tuple[tuple[str, int], ...]
    arithmetic_operations: tuple[tuple[str, int], ...]
    decider_comparators: tuple[tuple[str, int], ...]
    state_families: tuple[tuple[str, int], ...]
    abstract_signals: int
    signal_conflicts: int
    signal_aliases: int
    abstract_nets: int
    net_conflicts: int
    dynamic_vector_nets: int
    fixed_signal_nets: int
    nets_with_abstract_signals: int
    max_signals_per_net: int
    net_endpoint_histogram: tuple[tuple[str, int], ...]
    max_net_endpoints: int

    @property
    def total_entities(self) -> int:
        return self.implementation_entities + self.annotation_entities

    @property
    def phase_delay_entities(self) -> int:
        return sum(count for role, count in self.lowering_roles if role.startswith("phase-delay."))

    @property
    def state_implementation_entities(self) -> int:
        return sum(count for role, count in self.lowering_roles if role.startswith("state."))

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""

        result = asdict(self)
        result["total_entities"] = self.total_entities
        result["phase_delay_entities"] = self.phase_delay_entities
        result["state_implementation_entities"] = self.state_implementation_entities
        return result


def census_abstract_physical(circuit: abstract.AbstractPhysicalCircuit) -> AbstractPhysicalCensus:
    """Summarize entity, timing-artifact, state, lane, constraint, and net structure."""

    circuit.validate()
    entity_kinds: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    arithmetic: Counter[str] = Counter()
    deciders: Counter[str] = Counter()
    state_families: Counter[str] = Counter()

    annotation_entities = 0
    for entity in circuit.entities:
        entity_kinds[type(entity).__name__] += 1
        role = _lowering_role(entity)
        roles[role] += 1
        family = _state_family(entity)
        if family is not None:
            state_families[family] += 1

        if isinstance(entity, abstract.ConstantCombinator) and entity.annotation_only:
            annotation_entities += 1
        elif isinstance(entity, abstract.ArithmeticCombinator):
            arithmetic[entity.operation] += 1
        elif isinstance(entity, abstract.DeciderCombinator):
            deciders[entity.comparator] += 1

    endpoint_histogram: Counter[str] = Counter()
    max_net_endpoints = 0
    max_signals_per_net = 0
    for net in circuit.nets:
        endpoints = len(net.endpoints)
        endpoint_histogram[_endpoint_bucket(endpoints)] += 1
        max_net_endpoints = max(max_net_endpoints, endpoints)
        max_signals_per_net = max(max_signals_per_net, len(net.signals))

    return AbstractPhysicalCensus(
        implementation_entities=circuit.combinator_count,
        annotation_entities=annotation_entities,
        entity_kinds=_sorted_counts(entity_kinds),
        lowering_roles=_sorted_counts(roles),
        arithmetic_operations=_sorted_counts(arithmetic),
        decider_comparators=_sorted_counts(deciders),
        state_families=_sorted_counts(state_families),
        abstract_signals=len(circuit.signals),
        signal_conflicts=len(circuit.signal_conflicts),
        signal_aliases=len(circuit.signal_aliases),
        abstract_nets=len(circuit.nets),
        net_conflicts=len(circuit.net_conflicts),
        dynamic_vector_nets=sum(net.carries_dynamic_vector for net in circuit.nets),
        fixed_signal_nets=sum(bool(net.fixed_signals) for net in circuit.nets),
        nets_with_abstract_signals=sum(bool(net.signals) for net in circuit.nets),
        max_signals_per_net=max_signals_per_net,
        net_endpoint_histogram=tuple(
            (bucket, endpoint_histogram[bucket])
            for bucket in ("1", "2", "3-4", "5-8", "9-16", "17-32", "33+")
            if endpoint_histogram[bucket]
        ),
        max_net_endpoints=max_net_endpoints,
    )


def format_abstract_physical_census(census: AbstractPhysicalCensus) -> str:
    """Render a stable human-readable census suitable for benchmark logs."""

    lines = [
        "abstract physical census",
        (
            "  entities: "
            f"implementation={census.implementation_entities}; "
            f"annotation={census.annotation_entities}; total={census.total_entities}"
        ),
        (
            "  lanes/constraints: "
            f"signals={census.abstract_signals}; "
            f"signal_conflicts={census.signal_conflicts}; "
            f"signal_aliases={census.signal_aliases}; net_conflicts={census.net_conflicts}"
        ),
        (
            "  nets: "
            f"total={census.abstract_nets}; dynamic_vector={census.dynamic_vector_nets}; "
            f"fixed_signal={census.fixed_signal_nets}; "
            f"with_abstract_lanes={census.nets_with_abstract_signals}; "
            f"max_lanes={census.max_signals_per_net}; max_endpoints={census.max_net_endpoints}"
        ),
        (
            "  highlighted: "
            f"phase_delays={census.phase_delay_entities}; "
            f"state_implementation={census.state_implementation_entities}"
        ),
    ]
    _append_counts(lines, "entity kinds", census.entity_kinds)
    _append_counts(lines, "lowering roles", census.lowering_roles)
    _append_counts(lines, "state families", census.state_families)
    _append_counts(lines, "arithmetic operations", census.arithmetic_operations)
    _append_counts(lines, "decider comparators", census.decider_comparators)
    _append_counts(lines, "net endpoint histogram", census.net_endpoint_histogram)
    return "\n".join(lines)


def _lowering_role(entity: abstract.AbstractEntity) -> str:
    description = entity.description or ""
    if isinstance(entity, abstract.ConstantCombinator) and entity.annotation_only:
        return "annotation"
    if description == "phase alignment delay":
        return "phase-delay.scalar"
    if description == "vector phase alignment delay":
        return "phase-delay.vector"

    state = _STATE_DESCRIPTION.fullmatch(description)
    if state is not None:
        register_kind, _register_name, action = state.groups()
        if register_kind == "AccumulatorReg":
            if action == "active when clear=0":
                return "state.accumulator.clear-control"
            if action.startswith("gate add["):
                return "state.accumulator.add-gate"
            if action == "vector memory":
                return "state.accumulator.memory"
            if "enabled" in action:
                return "state.accumulator.add-control"
            return "state.accumulator.other"
        if action == "set!=0 -> pass":
            return "state.freeze.pass-control"
        if action == "set=0 -> hold":
            return "state.freeze.hold-control"
        if action == "transparent input gate":
            return "state.freeze.input-gate"
        if action == "vector memory":
            return "state.freeze.memory"
        return "state.freeze.other"

    if isinstance(entity, abstract.ConstantCombinator):
        return "constant"
    return "computation"


def _state_family(entity: abstract.AbstractEntity) -> str | None:
    state = _STATE_DESCRIPTION.fullmatch(entity.description or "")
    if state is None:
        return None
    register_kind, register_name, _action = state.groups()
    family = _NUMBERED_REGISTER_SUFFIX.sub("_*", register_name)
    return f"{register_kind}.{family}"


def _endpoint_bucket(count: int) -> str:
    if count <= 1:
        return "1"
    if count == 2:
        return "2"
    if count <= 4:
        return "3-4"
    if count <= 8:
        return "5-8"
    if count <= 16:
        return "9-16"
    if count <= 32:
        return "17-32"
    return "33+"


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
