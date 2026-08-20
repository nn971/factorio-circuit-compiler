"""Physical provider bindings for semantic oracle sources.

The deterministic semantic model observes oracle values but does not compute them.
Physical compilation resolves each oracle through an explicit provider before joint
signal allocation, wire-color assignment, placement, and routing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, runtime_checkable

from factorio_circuit.ir.abstract_physical import (
    AbstractEntity,
    AbstractNet,
    AbstractPhysicalCircuit,
    ConstantCombinator,
    Connector,
    Endpoint,
    EntityPlacementConstraint,
    EntityPlacementMode,
    InputPort,
    PhysicalAnchor,
    SelectorCombinator,
    SignalRef,
)
from factorio_circuit.ir.oracle import (
    OracleSource,
    VectorOracleInput,
    is_provider_input_port_name,
    oracle_sources,
    provider_input_port_name,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import CircuitModule


class OracleBindingError(ValueError):
    """Raised when oracle declarations and physical providers do not match."""


class OraclePortDisposition(StrEnum):
    """Whether provider materialization consumes the generated oracle boundary port."""

    CONSUMED = "consumed"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class FreePlacement:
    """Provider entity may participate freely in ordinary physical placement."""


@dataclass(frozen=True, slots=True)
class AnchoredPlacement:
    """Provider entity must be placed at one symbolic deployment anchor."""

    anchor: str

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, str) or not self.anchor:
            raise ValueError("anchored provider placement requires a non-empty anchor name")


ProviderPlacement = FreePlacement | AnchoredPlacement
FREE_PLACEMENT = FreePlacement()


@dataclass(frozen=True, slots=True)
class OracleProviderInput:
    """One deterministic physical net exposed as an input to an oracle provider."""

    name: str
    net_id: int
    signal: SignalRef | None
    phase: int


@dataclass(slots=True)
class OraclePhysicalContext:
    """Mutable, target-level context exposed to an oracle provider.

    Providers operate on an already-lowered :class:`AbstractPhysicalCircuit`, but before physical
    synthesis. Entity ids, oracle-net edits, provider-input taps, and placement constraints therefore
    participate in the ordinary joint synthesis/layout pass.
    """

    circuit: AbstractPhysicalCircuit
    source: OracleSource
    port: InputPort
    net_id: int
    _next_entity_id: int
    _consumed_provider_inputs: set[str]

    @property
    def is_vector(self) -> bool:
        return isinstance(self.source, VectorOracleInput)

    @property
    def signal(self) -> int | None:
        """Abstract scalar output lane, or ``None`` for a runtime-open vector oracle."""

        return self.port.signal

    @property
    def net(self) -> AbstractNet:
        return self.circuit.net_by_id(self.net_id)

    def new_entity_id(self) -> int:
        entity_id = self._next_entity_id
        self._next_entity_id += 1
        return entity_id

    def add_entity(
        self,
        entity: AbstractEntity,
        *,
        placement: ProviderPlacement = FREE_PLACEMENT,
    ) -> None:
        """Add one provider entity plus its physical placement contract."""

        if not isinstance(placement, (FreePlacement, AnchoredPlacement)):
            raise OracleBindingError(
                "provider placement must be FreePlacement or AnchoredPlacement"
            )
        self.circuit.entities.append(entity)
        if isinstance(placement, AnchoredPlacement):
            constraint = EntityPlacementConstraint(
                entity=entity.id,
                mode=EntityPlacementMode.ANCHORED,
                anchor=PhysicalAnchor(placement.anchor),
            )
        else:
            constraint = EntityPlacementConstraint(entity=entity.id)
        self.circuit.placement_constraints.append(constraint)

    def attach(self, endpoint: Endpoint) -> None:
        """Attach a provider endpoint to the oracle's output net."""

        net = self.net
        if endpoint in net.endpoints:
            return
        self._replace_net(replace(net, endpoints=(*net.endpoints, endpoint)))

    def provider_input(self, name: str) -> OracleProviderInput:
        """Resolve one named deterministic provider-input tap without consuming it yet."""

        port_name = provider_input_port_name(self.source.name, name)
        matches = [port for port in self.circuit.outputs if port.name == port_name]
        if len(matches) != 1:
            raise OracleBindingError(
                f"oracle {self.source.name!r} provider input {name!r} was not lowered exactly once; "
                "compile provider-input circuits through Circuit.compile()"
            )
        port = matches[0]
        matching_nets = [net for net in self.circuit.nets if port.endpoint in net.endpoints]
        if len(matching_nets) != 1:
            raise OracleBindingError(
                f"oracle {self.source.name!r} provider input {name!r} must belong to exactly one net"
            )
        return OracleProviderInput(name, matching_nets[0].id, port.signal, port.phase)

    def consume_input(self, name: str, endpoint: Endpoint) -> OracleProviderInput:
        """Attach ``endpoint`` to a deterministic provider-input net and consume its hidden port."""

        provider_input = self.provider_input(name)
        net = self.circuit.net_by_id(provider_input.net_id)
        if endpoint not in net.endpoints:
            self._replace_net(replace(net, endpoints=(*net.endpoints, endpoint)))
        self._consumed_provider_inputs.add(provider_input_port_name(self.source.name, name))
        return provider_input

    def add_fixed_signals(self, *signals: SignalId) -> None:
        """Declare concrete signal lanes additionally carried by a vector provider."""

        if not signals:
            return
        net = self.net
        merged = tuple(dict.fromkeys((*net.fixed_signals, *signals)))
        self._replace_net(replace(net, fixed_signals=merged))

    def _replace_net(self, updated: AbstractNet) -> None:
        for index, candidate in enumerate(self.circuit.nets):
            if candidate.id == updated.id:
                self.circuit.nets[index] = updated
                return
        raise OracleBindingError(f"oracle {self.source.name!r} references a missing net")


@runtime_checkable
class OracleProvider(Protocol):
    """Target provider that realizes one semantic oracle at the physical boundary."""

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        """Attach provider entities/wiring and describe boundary-port ownership."""

        ...


@dataclass(frozen=True, slots=True)
class ExternalOracleProvider:
    """Intentionally leave an oracle as a manually wired physical input boundary."""

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        del context
        return OraclePortDisposition.EXTERNAL


@dataclass(frozen=True, slots=True)
class ScalarConstantOracleProvider:
    """Small deterministic provider useful for probes/tests and fixed target observations."""

    value: int
    placement: ProviderPlacement = FREE_PLACEMENT

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise TypeError("scalar constant oracle value must be an integer")
        if not isinstance(self.placement, (FreePlacement, AnchoredPlacement)):
            raise TypeError("scalar constant oracle placement is invalid")

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        signal = context.signal
        if context.is_vector or signal is None:
            raise OracleBindingError(
                f"scalar constant provider cannot realize vector oracle {context.source.name!r}"
            )
        entity = ConstantCombinator(
            id=context.new_entity_id(),
            signals=((signal, self.value),),
            description=f"ORACLE {context.source.name}: constant {self.value}",
        )
        context.add_entity(entity, placement=self.placement)
        context.attach(Endpoint(entity.id, Connector.SINGLE))
        return OraclePortDisposition.CONSUMED


@dataclass(frozen=True, slots=True)
class VectorConstantOracleProvider:
    """Constant whole-signal-map provider, primarily for physical-provider verification."""

    signals: Mapping[SignalId, int]
    placement: ProviderPlacement = FREE_PLACEMENT

    def __post_init__(self) -> None:
        normalized: dict[SignalId, int] = {}
        for signal, value in self.signals.items():
            if not isinstance(signal, SignalId):
                raise TypeError("vector constant oracle keys must be SignalId values")
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("vector constant oracle values must be integers")
            if value != 0:
                normalized[signal] = value
        if not isinstance(self.placement, (FreePlacement, AnchoredPlacement)):
            raise TypeError("vector constant oracle placement is invalid")
        object.__setattr__(self, "signals", normalized)

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        if not context.is_vector or context.signal is not None:
            raise OracleBindingError(
                f"vector constant provider cannot realize scalar oracle {context.source.name!r}"
            )
        ordered = tuple(
            sorted(self.signals.items(), key=lambda item: (item[0].kind, item[0].name))
        )
        entity = ConstantCombinator(
            id=context.new_entity_id(),
            signals=ordered,
            description=f"ORACLE {context.source.name}: constant vector",
        )
        context.add_entity(entity, placement=self.placement)
        context.attach(Endpoint(entity.id, Connector.SINGLE))
        context.add_fixed_signals(*(signal for signal, _ in ordered))
        return OraclePortDisposition.CONSUMED


@dataclass(frozen=True, slots=True)
class RandomSignalOracleProvider:
    """Realize a vector oracle with a selector combinator in Random Input mode."""

    input_name: str = "candidates"
    update_interval: int = 1
    placement: ProviderPlacement = FREE_PLACEMENT

    def __post_init__(self) -> None:
        if not isinstance(self.input_name, str) or not self.input_name:
            raise ValueError("random selector provider input name must be non-empty")
        if (
            isinstance(self.update_interval, bool)
            or not isinstance(self.update_interval, int)
            or not 1 <= self.update_interval <= 0xFFFFFFFF
        ):
            raise ValueError("random selector update_interval must be in [1, 2^32-1]")
        if not isinstance(self.placement, (FreePlacement, AnchoredPlacement)):
            raise TypeError("random selector oracle placement is invalid")

    def materialize(self, context: OraclePhysicalContext) -> OraclePortDisposition:
        if not context.is_vector or context.signal is not None:
            raise OracleBindingError(
                f"random selector provider requires a vector oracle, got {context.source.name!r}"
            )
        entity_id = context.new_entity_id()
        provider_input = context.consume_input(
            self.input_name,
            Endpoint(entity_id, Connector.INPUT),
        )
        if provider_input.signal is not None:
            raise OracleBindingError(
                "random selector candidates must be a whole-vector provider input"
            )
        entity = SelectorCombinator(
            id=entity_id,
            operation="random",
            input_nets=(provider_input.net_id,),
            random_update_interval=self.update_interval,
            description=(
                f"ORACLE {context.source.name}: random signal every {self.update_interval} tick(s)"
            ),
        )
        context.add_entity(entity, placement=self.placement)
        context.attach(Endpoint(entity.id, Connector.OUTPUT))
        return OraclePortDisposition.CONSUMED


def validate_oracle_provider_bindings(
    module: CircuitModule,
    providers: Mapping[str, OracleProvider] | None,
) -> dict[str, OracleProvider]:
    """Validate exact provider coverage for all declared semantic oracles."""

    sources = oracle_sources(module)
    names = {source.name for source in sources}
    bindings = dict(providers or {})
    missing = sorted(names - bindings.keys())
    extra = sorted(bindings.keys() - names)
    if missing:
        raise OracleBindingError(
            "physical compilation requires an explicit provider for every oracle; missing: "
            + ", ".join(repr(name) for name in missing)
        )
    if extra:
        raise OracleBindingError(
            "oracle provider bindings reference undeclared oracle(s): "
            + ", ".join(repr(name) for name in extra)
        )
    for name, provider in bindings.items():
        if not isinstance(provider, OracleProvider):
            raise OracleBindingError(
                f"provider for oracle {name!r} does not implement OracleProvider.materialize()"
            )
    return bindings


def _remove_annotation_marker(circuit: AbstractPhysicalCircuit, entity_id: int) -> None:
    entity = circuit.entity_by_id(entity_id)
    if not isinstance(entity, ConstantCombinator) or not entity.annotation_only:
        raise OracleBindingError(
            f"internal oracle boundary entity {entity_id} is not an annotation marker"
        )
    circuit.entities = [candidate for candidate in circuit.entities if candidate.id != entity_id]
    circuit.placement_constraints = [
        constraint
        for constraint in circuit.placement_constraints
        if constraint.entity != entity_id
    ]
    circuit.nets = [
        replace(
            net,
            endpoints=tuple(
                endpoint for endpoint in net.endpoints if endpoint.entity != entity_id
            ),
        )
        for net in circuit.nets
    ]


def materialize_oracle_providers(
    module: CircuitModule,
    circuit: AbstractPhysicalCircuit,
    providers: Mapping[str, OracleProvider] | None,
) -> None:
    """Bind all semantic oracles into the abstract physical graph in place."""

    bindings = validate_oracle_provider_bindings(module, providers)
    if not bindings:
        return

    ports = {port.name: port for port in circuit.inputs}
    next_entity_id = max((entity.id for entity in circuit.entities), default=0) + 1
    consumed_oracle_ports: list[InputPort] = []
    consumed_provider_inputs: set[str] = set()

    for source in oracle_sources(module):
        try:
            port = ports[source.name]
        except KeyError as exc:
            raise OracleBindingError(
                f"physical lowering did not expose a boundary for oracle {source.name!r}"
            ) from exc
        matching_nets = [net for net in circuit.nets if port.endpoint in net.endpoints]
        if len(matching_nets) != 1:
            raise OracleBindingError(
                f"oracle {source.name!r} must belong to exactly one abstract physical net"
            )
        context = OraclePhysicalContext(
            circuit=circuit,
            source=source,
            port=port,
            net_id=matching_nets[0].id,
            _next_entity_id=next_entity_id,
            _consumed_provider_inputs=consumed_provider_inputs,
        )
        disposition = bindings[source.name].materialize(context)
        next_entity_id = context._next_entity_id
        if not isinstance(disposition, OraclePortDisposition):
            raise OracleBindingError(
                f"provider for oracle {source.name!r} returned an invalid port disposition"
            )
        if disposition is OraclePortDisposition.CONSUMED:
            consumed_oracle_ports.append(port)

    hidden_ports = [
        port for port in circuit.outputs if is_provider_input_port_name(port.name)
    ]
    unconsumed = sorted(
        port.name for port in hidden_ports if port.name not in consumed_provider_inputs
    )
    if unconsumed:
        raise OracleBindingError(
            "oracle provider input tap(s) were not consumed by their provider: "
            + ", ".join(repr(name) for name in unconsumed)
        )

    if consumed_oracle_ports:
        consumed_names = {port.name for port in consumed_oracle_ports}
        circuit.inputs = [port for port in circuit.inputs if port.name not in consumed_names]
        for port in consumed_oracle_ports:
            _remove_annotation_marker(circuit, port.endpoint.entity)

    if hidden_ports:
        hidden_names = {port.name for port in hidden_ports}
        circuit.outputs = [port for port in circuit.outputs if port.name not in hidden_names]
        for port in hidden_ports:
            _remove_annotation_marker(circuit, port.endpoint.entity)

    circuit.validate()
