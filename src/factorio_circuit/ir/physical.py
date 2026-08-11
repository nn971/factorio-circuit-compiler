"""Ready-to-layout physical Factorio circuit representation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class WireColor(StrEnum):
    RED = "red"
    GREEN = "green"


class Connector(StrEnum):
    SINGLE = "single"
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True, order=True)
class SignalId:
    """A concrete Factorio signal identity."""

    kind: str
    name: str


@dataclass(frozen=True, slots=True)
class Operand:
    signal: SignalId | None = None
    constant: int | None = None
    each: bool = False
    networks: tuple[WireColor, ...] | None = None

    def __post_init__(self) -> None:
        chosen = sum((self.signal is not None, self.constant is not None, self.each))
        if chosen != 1:
            raise ValueError("operand must contain exactly one of signal, constant, or each")


@dataclass(frozen=True, slots=True)
class ArithmeticCombinator:
    id: int
    operation: str
    left: Operand
    right: Operand
    output_each: bool
    output_signal: SignalId | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class DeciderCombinator:
    id: int
    comparator: str
    left: Operand
    right: Operand
    output_signal: SignalId
    output_constant: int = 1
    output_copy_count_from_input: bool = False
    output_networks: tuple[WireColor, ...] | None = None
    else_output_signal: SignalId | None = None
    else_output_constant: int = 1
    else_copy_count_from_input: bool = False
    else_output_networks: tuple[WireColor, ...] | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ConstantCombinator:
    id: int
    signals: tuple[tuple[SignalId, int], ...] = ()
    description: str | None = None
    annotation_only: bool = False


PhysicalEntity = ArithmeticCombinator | DeciderCombinator | ConstantCombinator


@dataclass(frozen=True, slots=True)
class WireEndpoint:
    entity: int
    connector: Connector


@dataclass(frozen=True, slots=True)
class WireConnection:
    source: WireEndpoint
    target: WireEndpoint
    color: WireColor = WireColor.RED


@dataclass(frozen=True, slots=True)
class InputPort:
    name: str
    marker_entity: int
    signal: SignalId | None


@dataclass(frozen=True, slots=True)
class OutputPort:
    name: str
    marker_entity: int
    signal: SignalId | None
    phase: int


@dataclass(slots=True)
class PhysicalCircuit:
    name: str
    entities: list[PhysicalEntity] = field(default_factory=list)
    connections: list[WireConnection] = field(default_factory=list)
    inputs: list[InputPort] = field(default_factory=list)
    outputs: list[OutputPort] = field(default_factory=list)

    @property
    def combinator_count(self) -> int:
        """Count implementation combinators, excluding I/O annotation markers."""

        return sum(
            not (isinstance(entity, ConstantCombinator) and entity.annotation_only)
            for entity in self.entities
        )

    @property
    def blueprint_entity_count(self) -> int:
        return len(self.entities)

    @property
    def input_signals(self) -> dict[str, SignalId]:
        return {port.name: port.signal for port in self.inputs if port.signal is not None}

    @property
    def output_signals(self) -> tuple[SignalId, ...]:
        return tuple(port.signal for port in self.outputs if port.signal is not None)

    @property
    def output_phases(self) -> tuple[int, ...]:
        return tuple(port.phase for port in self.outputs)

    def entity_by_id(self, entity_id: int) -> PhysicalEntity:
        for entity in self.entities:
            if entity.id == entity_id:
                return entity
        raise KeyError(entity_id)
