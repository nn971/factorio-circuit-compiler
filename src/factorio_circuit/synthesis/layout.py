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
        return (values[0], values[1], values[2], values[3])


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

    @property
    def concrete_signal_count(self) -> int:
        """Number of distinct Factorio signal identities used for abstract lanes."""

        return len(set(self.allocated_signals.values()))

    @property
    def reused_signal_groups(self) -> dict[SignalId, tuple[int, ...]]:
        """Concrete identities shared by two or more abstract signal lanes."""

        members: dict[SignalId, list[int]] = {}
        for abstract_id, concrete in self.signal_allocation:
            members.setdefault(concrete, []).append(abstract_id)
        return {
            concrete: tuple(sorted(abstract_ids))
            for concrete, abstract_ids in members.items()
            if len(abstract_ids) > 1
        }

    @property
    def signal_slots_saved(self) -> int:
        """Concrete signal identities avoided by reuse versus one-per-abstract-lane."""

        return len(self.signal_allocation) - self.concrete_signal_count
