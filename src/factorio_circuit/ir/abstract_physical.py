"""Abstract physical IR for target-level Factorio synthesis.

This layer represents exact target combinator behavior while keeping late physical
resources unresolved. Signal identities, red/green wire colors, net merging, and
entity coordinates are decisions for physical synthesis.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from factorio_circuit.ir.physical import SignalId


class Connector(StrEnum):
    """Logical Factorio connector kind before wire-color assignment."""

    SINGLE = "single"
    INPUT = "input"
    OUTPUT = "output"


class SignalDomain(StrEnum):
    """Allowed target namespace for a late-allocated signal identity."""

    ANY = "any"
    VIRTUAL = "virtual"
    ITEM = "item"
    FLUID = "fluid"


@dataclass(frozen=True, slots=True, order=True)
class AbstractSignal:
    """A late-allocated signal-lane variable.

    ``id`` is IR identity, rather than a Factorio signal name. Physical synthesis may
    map compatible abstract signals to the same concrete Factorio identity when their
    electrical lifetimes permit it.
    """

    id: int
    label: str | None = None
    domain: SignalDomain = SignalDomain.ANY


SignalRef = int | SignalId


@dataclass(frozen=True, slots=True, order=True)
class Endpoint:
    entity: int
    connector: Connector


@dataclass(frozen=True, slots=True)
class AbstractNet:
    """One logical electrical-connectivity requirement.

    ``signals`` records compiler-allocated abstract lanes known to coexist on this net.
    ``fixed_signals`` records user/target-selected concrete lanes such as item signals.
    ``carries_dynamic_vector`` means the net may additionally carry arbitrary runtime
    lanes, as whole-vector external inputs do.

    Signal identities and electrical connectivity remain independent resources: one
    net may carry many lanes, and one lane identity may appear on several disconnected
    nets.
    """

    id: int
    signals: tuple[int, ...]
    endpoints: tuple[Endpoint, ...]
    label: str | None = None
    fixed_signals: tuple[SignalId, ...] = ()
    carries_dynamic_vector: bool = False


@dataclass(frozen=True, slots=True)
class SignalConflict:
    """Forbid two abstract signals from sharing one concrete Factorio identity."""

    left: int
    right: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SignalAlias:
    """Require two abstract lanes to share one concrete Factorio signal identity."""

    left: int
    right: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class NetConflict:
    """Forbid two abstract nets from being electrically merged."""

    left: int
    right: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Operand:
    """Target combinator operand using abstract/fixed signals and logical input nets."""

    signal: SignalRef | None = None
    constant: int | None = None
    each: bool = False
    nets: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        chosen = sum((self.signal is not None, self.constant is not None, self.each))
        if chosen != 1:
            raise ValueError("operand must contain exactly one of signal, constant, or each")
        dynamic = self.signal is not None or self.each
        if dynamic and not self.nets:
            raise ValueError("dynamic operands must select at least one abstract net")
        if self.constant is not None and self.nets:
            raise ValueError("constant operands cannot select input nets")


@dataclass(frozen=True, slots=True)
class ArithmeticCombinator:
    id: int
    operation: str
    left: Operand
    right: Operand
    output_each: bool
    output_signal: int | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if self.output_each == (self.output_signal is not None):
            raise ValueError("arithmetic output must be exactly one of Each or an abstract signal")


@dataclass(frozen=True, slots=True)
class DeciderCondition:
    """Additional Factorio decider condition joined to the first condition."""

    comparator: str
    left: Operand
    right: Operand
    compare_type: str = "and"

    def __post_init__(self) -> None:
        if self.compare_type not in {"and", "or"}:
            raise ValueError("decider compare_type must be 'and' or 'or'")


@dataclass(frozen=True, slots=True)
class DeciderOutput:
    """One additional normal output of a Factorio decider combinator."""

    signal: int
    constant: int = 1
    copy_count_from_input: bool = False
    copy_count_nets: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.copy_count_nets and not self.copy_count_from_input:
            raise ValueError("copy-count net selection requires copy_count_from_input")


@dataclass(frozen=True, slots=True)
class DeciderCombinator:
    id: int
    comparator: str
    left: Operand
    right: Operand
    output_signal: int
    output_constant: int = 1
    output_copy_count_from_input: bool = False
    copy_count_nets: tuple[int, ...] = ()
    additional_conditions: tuple[DeciderCondition, ...] = ()
    additional_outputs: tuple[DeciderOutput, ...] = ()
    else_output_signal: int | None = None
    else_output_constant: int = 1
    else_copy_count_from_input: bool = False
    else_copy_count_nets: tuple[int, ...] = ()
    description: str | None = None

    def __post_init__(self) -> None:
        if self.copy_count_nets and not self.output_copy_count_from_input:
            raise ValueError("copy-count net selection requires output_copy_count_from_input")
        if self.else_copy_count_nets and not self.else_copy_count_from_input:
            raise ValueError("else copy-count net selection requires else_copy_count_from_input")


@dataclass(frozen=True, slots=True)
class ConstantCombinator:
    id: int
    signals: tuple[tuple[SignalRef, int], ...] = ()
    description: str | None = None
    annotation_only: bool = False


AbstractEntity = ArithmeticCombinator | DeciderCombinator | ConstantCombinator


@dataclass(frozen=True, slots=True)
class InputPort:
    name: str
    endpoint: Endpoint
    signal: int | None


@dataclass(frozen=True, slots=True)
class OutputPort:
    name: str
    endpoint: Endpoint
    signal: SignalRef | None
    phase: int


@dataclass(slots=True)
class AbstractPhysicalCircuit:
    """Target combinator graph before joint signal/wire/layout synthesis."""

    name: str
    signals: list[AbstractSignal] = field(default_factory=list)
    entities: list[AbstractEntity] = field(default_factory=list)
    nets: list[AbstractNet] = field(default_factory=list)
    signal_conflicts: list[SignalConflict] = field(default_factory=list)
    signal_aliases: list[SignalAlias] = field(default_factory=list)
    net_conflicts: list[NetConflict] = field(default_factory=list)
    inputs: list[InputPort] = field(default_factory=list)
    outputs: list[OutputPort] = field(default_factory=list)

    @property
    def combinator_count(self) -> int:
        """Count implementation combinators, excluding annotation-only constants."""

        return sum(
            not (isinstance(entity, ConstantCombinator) and entity.annotation_only)
            for entity in self.entities
        )

    def entity_by_id(self, entity_id: int) -> AbstractEntity:
        for entity in self.entities:
            if entity.id == entity_id:
                return entity
        raise KeyError(entity_id)

    def signal_by_id(self, signal_id: int) -> AbstractSignal:
        for signal in self.signals:
            if signal.id == signal_id:
                return signal
        raise KeyError(signal_id)

    def net_by_id(self, net_id: int) -> AbstractNet:
        for net in self.nets:
            if net.id == net_id:
                return net
        raise KeyError(net_id)

    def validate(self) -> None:
        """Validate local referential and compatibility invariants."""

        entity_ids = _unique_ids("entity", (entity.id for entity in self.entities))
        signal_ids = _unique_ids("signal", (signal.id for signal in self.signals))
        net_ids = _unique_ids("net", (net.id for net in self.nets))

        for net in self.nets:
            if len(set(net.signals)) != len(net.signals):
                raise ValueError(f"net {net.id} contains a duplicate signal")
            if len(set(net.fixed_signals)) != len(net.fixed_signals):
                raise ValueError(f"net {net.id} contains a duplicate fixed signal")
            if len(set(net.endpoints)) != len(net.endpoints):
                raise ValueError(f"net {net.id} contains a duplicate endpoint")
            for signal_id in net.signals:
                _require(signal_ids, signal_id, "signal")
            for endpoint in net.endpoints:
                if endpoint.entity not in entity_ids:
                    raise ValueError(f"net {net.id} references unknown entity {endpoint.entity}")

        connected_entities = {
            endpoint.entity
            for net in self.nets
            if len(net.endpoints) >= 2
            for endpoint in net.endpoints
        }

        for entity in self.entities:
            if (
                isinstance(entity, ConstantCombinator)
                and entity.signals
                and not entity.annotation_only
                and entity.id not in connected_entities
            ):
                raise ValueError(
                    f"constant combinator {entity.id} produces signals but is electrically orphaned"
                )
            if isinstance(entity, ArithmeticCombinator):
                self._validate_operand(entity.left, signal_ids, net_ids)
                self._validate_operand(entity.right, signal_ids, net_ids)
                if entity.output_signal is not None:
                    _require(signal_ids, entity.output_signal, "signal")
            elif isinstance(entity, DeciderCombinator):
                self._validate_operand(entity.left, signal_ids, net_ids)
                self._validate_operand(entity.right, signal_ids, net_ids)
                _require(signal_ids, entity.output_signal, "signal")
                self._validate_net_refs(entity.copy_count_nets, net_ids)
                for condition in entity.additional_conditions:
                    self._validate_operand(condition.left, signal_ids, net_ids)
                    self._validate_operand(condition.right, signal_ids, net_ids)
                for output in entity.additional_outputs:
                    _require(signal_ids, output.signal, "signal")
                    self._validate_net_refs(output.copy_count_nets, net_ids)
                if entity.else_output_signal is not None:
                    _require(signal_ids, entity.else_output_signal, "signal")
                    self._validate_net_refs(entity.else_copy_count_nets, net_ids)
            else:
                for signal_ref, _count in entity.signals:
                    self._validate_signal_ref(signal_ref, signal_ids)

        conflict_pairs: set[tuple[int, int]] = set()
        for signal_conflict in self.signal_conflicts:
            self._validate_conflict(
                signal_conflict.left, signal_conflict.right, signal_ids, "signal"
            )
            conflict_pairs.add(
                (
                    min(signal_conflict.left, signal_conflict.right),
                    max(signal_conflict.left, signal_conflict.right),
                )
            )
        for signal_alias in self.signal_aliases:
            self._validate_conflict(signal_alias.left, signal_alias.right, signal_ids, "signal")
            if (
                min(signal_alias.left, signal_alias.right),
                max(signal_alias.left, signal_alias.right),
            ) in conflict_pairs:
                raise ValueError("signal pair cannot be both aliased and conflicting")
        for net_conflict in self.net_conflicts:
            self._validate_conflict(net_conflict.left, net_conflict.right, net_ids, "net")

        for input_port in self.inputs:
            if input_port.endpoint.entity not in entity_ids:
                raise ValueError(f"port {input_port.name!r} references unknown entity")
            if input_port.signal is not None:
                self._validate_signal_ref(input_port.signal, signal_ids)
        for output_port in self.outputs:
            if output_port.endpoint.entity not in entity_ids:
                raise ValueError(f"port {output_port.name!r} references unknown entity")
            if output_port.signal is not None:
                self._validate_signal_ref(output_port.signal, signal_ids)

    def _validate_operand(self, operand: Operand, signal_ids: set[int], net_ids: set[int]) -> None:
        if operand.signal is not None:
            self._validate_signal_ref(operand.signal, signal_ids)
        self._validate_net_refs(operand.nets, net_ids)

    @staticmethod
    def _validate_signal_ref(signal: SignalRef, signal_ids: set[int]) -> None:
        if isinstance(signal, int):
            _require(signal_ids, signal, "signal")
        elif not isinstance(signal, SignalId):  # pragma: no cover - defensive runtime check
            raise TypeError(signal)

    @staticmethod
    def _validate_net_refs(net_refs: tuple[int, ...], net_ids: set[int]) -> None:
        if len(set(net_refs)) != len(net_refs):
            raise ValueError("net selection contains a duplicate net")
        for net_id in net_refs:
            _require(net_ids, net_id, "net")

    @staticmethod
    def _validate_conflict(left: int, right: int, ids: set[int], kind: str) -> None:
        _require(ids, left, kind)
        _require(ids, right, kind)
        if left == right:
            raise ValueError(f"{kind} conflict cannot refer to the same {kind} twice")


def _unique_ids(kind: str, values: Iterable[int]) -> set[int]:
    ids: set[int] = set()
    for value in values:
        if value in ids:
            raise ValueError(f"duplicate {kind} id {value}")
        ids.add(value)
    return ids


def _require(ids: set[int], value: int, kind: str) -> None:
    if value not in ids:
        raise ValueError(f"unknown {kind} id {value}")
