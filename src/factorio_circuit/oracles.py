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
)
from factorio_circuit.ir.oracle import OracleSource, VectorOracleInput, oracle_sources
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


@dataclass(slots=True)
class OraclePhysicalContext:
    """Mutable, target-level context exposed to an oracle provider.

    Providers operate on an already-lowered :class:`AbstractPhysicalCircuit`, but before
    physical synthesis. Entity ids, oracle-net edits, and placement constraints performed here
    therefore participate in the ordinary joint synthesis/layout pass.
    """

    circuit: AbstractPhysicalCircuit
    source: OracleSource
    port: InputPort
    net_id: int
    _next_entity_id: int

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
        """Add one provider entity plus its physical placement contract.

        Ordinary compiler entities remain implicitly free. Provider entities record their
        intended freedom explicitly so target devices can be anchored without teaching the
        deterministic semantic layer anything about coordinates.
        """

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
        """Attach provider entities/wiring and describe boundary-port ownership.

        The provider must present a value that is stable at the oracle's semantic observation
        boundary. Providers whose target implementation has intra-step latency must buffer or
        otherwise satisfy that boundary contract themselves.
        """

        ...


@dataclass(frozen=True, slots=True)
class ExternalOracleProvider:
    """Intentionally leave an oracle as a manually wired physical input boundary.

    This is useful for world observations such as stock or temperature before a dedicated
    compiler-owned device provider exists. The semantic source remains an Oracle, rather than
    being silently downgraded to an ordinary external input.
    """

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
    consumed: set[str] = set()

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
        )
        disposition = bindings[source.name].materialize(context)
        next_entity_id = context._next_entity_id
        if not isinstance(disposition, OraclePortDisposition):
            raise OracleBindingError(
                f"provider for oracle {source.name!r} returned an invalid port disposition"
            )
        if disposition is OraclePortDisposition.CONSUMED:
            consumed.add(source.name)

    if consumed:
        circuit.inputs = [port for port in circuit.inputs if port.name not in consumed]
    circuit.validate()
