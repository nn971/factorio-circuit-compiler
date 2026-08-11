"""Final placement object produced by physical synthesis."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.physical import PhysicalCircuit, SignalId, WireColor


@dataclass(frozen=True, slots=True)
class LayoutRelay:
    entity_id: int
    position: tuple[float, float]
    description: str


@dataclass(frozen=True, slots=True)
class LayoutWire:
    source_entity: int
    source_connector_id: int
    target_entity: int
    target_connector_id: int
    color: WireColor

    def as_factorio_tuple(self) -> tuple[int, int, int, int]:
        values = [
            self.source_entity,
            self.source_connector_id,
            self.target_entity,
            self.target_connector_id,
        ]
        if values[0] > values[2]:
            values = [values[2], values[3], values[0], values[1]]
        return tuple(values)


@dataclass(frozen=True, slots=True)
class Layout:
    """Final concrete placement, routing, and allocation choices for a circuit."""

    circuit: PhysicalCircuit
    positions: dict[int, tuple[float, float]]
    relays: tuple[LayoutRelay, ...]
    wires: tuple[LayoutWire, ...]
    signal_allocation: tuple[tuple[int, SignalId], ...]
    net_colors: tuple[tuple[int, WireColor], ...]
    net_groups: tuple[tuple[int, int], ...] = ()

    @property
    def name(self) -> str:
        return self.circuit.name

    @property
    def combinator_count(self) -> int:
        return self.circuit.combinator_count

    @property
    def blueprint_entity_count(self) -> int:
        return len(self.circuit.entities) + len(self.relays)

    @property
    def allocated_signals(self) -> dict[int, SignalId]:
        return dict(self.signal_allocation)

    @property
    def assigned_net_colors(self) -> dict[int, WireColor]:
        return dict(self.net_colors)

    @property
    def coalesced_net_groups(self) -> dict[int, int]:
        """Map each abstract net id to its synthesized physical network group."""

        return dict(self.net_groups)

    @property
    def physical_net_count(self) -> int:
        """Number of distinct electrical groups after shared-connector coalescing."""

        return len(set(self.coalesced_net_groups.values()))
