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
class DeciderCondition:
    comparator: str
    left: Operand
    right: Operand
    compare_type: str = "and"

    def __post_init__(self) -> None:
        if self.compare_type not in {"and", "or"}:
            raise ValueError("decider compare_type must be 'and' or 'or'")


@dataclass(frozen=True, slots=True)
class DeciderOutput:
    signal: SignalId
    constant: int = 1
    copy_count_from_input: bool = False
    output_networks: tuple[WireColor, ...] | None = None


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
    additional_conditions: tuple[DeciderCondition, ...] = ()
    additional_outputs: tuple[DeciderOutput, ...] = ()
    else_output_signal: SignalId | None = None
    else_output_constant: int = 1
    else_copy_count_from_input: bool = False
    else_output_networks: tuple[WireColor, ...] | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True, init=False)
class SelectorCombinator(ArithmeticCombinator):
    """Concrete selector entity with ordinary combinator geometry/connectors.

    Inheriting the geometric shell keeps existing placement/routing code reusable. The selector is
    still a distinct runtime type and is never serialized or interpreted as arithmetic.
    """

    select_max: bool = True
    index: int = 0
    random_update_interval: int = 1

    def __init__(
        self,
        id: int,
        operation: str,
        *,
        select_max: bool = True,
        index: int = 0,
        random_update_interval: int = 1,
        description: str | None = None,
    ) -> None:
        if operation not in {"select", "random"}:
            raise ValueError(f"unsupported selector operation {operation!r}")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "left", Operand(each=True))
        object.__setattr__(self, "right", Operand(constant=index))
        object.__setattr__(self, "output_each", True)
        object.__setattr__(self, "output_signal", None)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "select_max", select_max)
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "random_update_interval", random_update_interval)


@dataclass(frozen=True, slots=True)
class ConstantCombinator:
    id: int
    signals: tuple[tuple[SignalId, int], ...] = ()
    description: str | None = None
    annotation_only: bool = False


@dataclass(frozen=True, slots=True, init=False)
class OpaqueSingleConnectorEntity(ConstantCombinator):
    """Serialized Factorio entity with one red/green circuit connector.

    Opaque entities let physical placement preserve reusable external-device entities without
    pretending that synthesis understands their runtime behavior. ``blueprint_fields`` stores the
    entity payload other than number/name/position. The explicit physical half-extent is retained
    for component-level prototype-aware validation.
    """

    prototype: str
    blueprint_fields: dict[str, object]
    physical_half_extent: tuple[float, float]

    def __init__(
        self,
        id: int,
        prototype: str,
        blueprint_fields: dict[str, object],
        *,
        physical_half_extent: tuple[float, float],
    ) -> None:
        if not prototype:
            raise ValueError("opaque physical entity prototype must be non-empty")
        if physical_half_extent[0] <= 0.0 or physical_half_extent[1] <= 0.0:
            raise ValueError("opaque physical entity half-extents must be positive")
        description = blueprint_fields.get("player_description")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "signals", ())
        object.__setattr__(self, "description", description if isinstance(description, str) else None)
        object.__setattr__(self, "annotation_only", False)
        object.__setattr__(self, "prototype", prototype)
        object.__setattr__(self, "blueprint_fields", blueprint_fields)
        object.__setattr__(self, "physical_half_extent", physical_half_extent)


@dataclass(frozen=True, slots=True, init=False)
class OpaqueDualConnectorEntity(ArithmeticCombinator):
    """Serialized Factorio entity with distinct input and output circuit connectors."""

    prototype: str
    blueprint_fields: dict[str, object]
    physical_half_extent: tuple[float, float]

    def __init__(
        self,
        id: int,
        prototype: str,
        blueprint_fields: dict[str, object],
        *,
        physical_half_extent: tuple[float, float],
    ) -> None:
        if not prototype:
            raise ValueError("opaque physical entity prototype must be non-empty")
        if physical_half_extent[0] <= 0.0 or physical_half_extent[1] <= 0.0:
            raise ValueError("opaque physical entity half-extents must be positive")
        description = blueprint_fields.get("player_description")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "operation", "opaque")
        object.__setattr__(self, "left", Operand(constant=0))
        object.__setattr__(self, "right", Operand(constant=0))
        object.__setattr__(self, "output_each", True)
        object.__setattr__(self, "output_signal", None)
        object.__setattr__(self, "description", description if isinstance(description, str) else None)
        object.__setattr__(self, "prototype", prototype)
        object.__setattr__(self, "blueprint_fields", blueprint_fields)
        object.__setattr__(self, "physical_half_extent", physical_half_extent)


PhysicalEntity = (
    ArithmeticCombinator
    | DeciderCombinator
    | SelectorCombinator
    | ConstantCombinator
    | OpaqueSingleConnectorEntity
    | OpaqueDualConnectorEntity
)


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
        """Count compiler implementation combinators, excluding opaque device entities and markers."""

        return sum(
            not isinstance(
                entity,
                (OpaqueSingleConnectorEntity, OpaqueDualConnectorEntity),
            )
            and not (isinstance(entity, ConstantCombinator) and entity.annotation_only)
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