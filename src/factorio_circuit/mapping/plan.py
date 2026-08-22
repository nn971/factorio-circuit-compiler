"""Selected realization plan produced by temporal technology mapping.

A plan is target-level enough to say which implementation candidate realizes each semantic recipe,
when its ports are used, which state-cell implementation realizes each periodic register, which exact
lifetimes need storage, and which explicit shared-resource mechanisms were selected. It deliberately
stops before concrete Factorio signal identities, red/green wiring, placement, and routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DeliveryKind(StrEnum):
    """How one selected producer realization satisfies one semantic use."""

    REUSE = "reuse"
    OBSERVE_AT = "observe-at"
    PRIVATE_TRANSPORT = "private-transport"
    BUS_TRANSPORT = "bus-transport"


@dataclass(frozen=True, slots=True)
class SelectedRealization:
    operation: int
    candidate: int
    output_phase: int
    entity_cost: int


@dataclass(frozen=True, slots=True)
class SelectedStateCell:
    """One selected periodic state-cell implementation.

    ``base_read_phase`` is the physical phase of logical occurrence zero. Other occurrence read
    phases are derived by adding the prescribed logical period.
    """

    register_name: str
    candidate: int
    base_read_phase: int
    entity_cost: int


@dataclass(frozen=True, slots=True)
class PeriodicCommitResource:
    """Shared modulo clock plus startup-ready latch for one periodic state domain.

    The resource uses a constant +1 source and modulo counter for the repeating cadence, plus one
    self-latching decider that becomes ready at tick ``period - 2``. State-cell control deciders
    absorb the ready and per-register clock-residue predicates as additional conditions, so the
    shared resource has no per-register entity surcharge.
    """

    period: int
    entity_cost: int = 3

    def __post_init__(self) -> None:
        if isinstance(self.period, bool) or not isinstance(self.period, int) or self.period < 3:
            raise ValueError("periodic commit resource requires period >= 3")
        if self.entity_cost != 3:
            raise ValueError("first periodic commit resource has exactly three entities")

    @property
    def ready_phase(self) -> int:
        return self.period - 2


@dataclass(frozen=True, slots=True)
class PlannedDelivery:
    producer: int
    consumer: int
    operand_index: int | None
    phase: int
    kind: DeliveryKind
    transport_start_phase: int | None = None

    @property
    def transport_length(self) -> int:
        if self.kind not in {DeliveryKind.PRIVATE_TRANSPORT, DeliveryKind.BUS_TRANSPORT}:
            return 0
        if self.transport_start_phase is None:
            raise ValueError("transport delivery has no start phase")
        return self.phase - self.transport_start_phase


@dataclass(frozen=True, slots=True)
class ExactLifetime:
    """One exact semantic token lifetime before its transport realization is chosen."""

    producer: int
    start_phase: int
    end_phase: int
    tap_phases: tuple[int, ...]

    @property
    def length(self) -> int:
        return self.end_phase - self.start_phase


@dataclass(frozen=True, slots=True)
class WireSumResource:
    """Intentional same-carrier output-network aggregation selected by the mapper."""

    operation: int
    left_producer: int
    right_producer: int
    phase: int


@dataclass(frozen=True, slots=True)
class DelayBusLane:
    """One scalar exact token assigned to an isolated shared delay bus."""

    producer: int
    start_phase: int
    end_phase: int
    delivery_phases: tuple[int, ...]

    @property
    def ingress_phase(self) -> int:
        return self.start_phase + 1

    @property
    def trunk_end_phase(self) -> int:
        return self.end_phase - 1

    @property
    def interface_combinators(self) -> int:
        return 1 + len(self.delivery_phases)


@dataclass(frozen=True, slots=True)
class DelayBusResource:
    """One continuous Each+0 shared middle with signal-isolated scalar lanes."""

    index: int
    middle_start_phase: int
    middle_end_phase: int
    lanes: tuple[DelayBusLane, ...]

    @property
    def middle_stages(self) -> int:
        return self.middle_end_phase - self.middle_start_phase

    @property
    def interface_combinators(self) -> int:
        return sum(lane.interface_combinators for lane in self.lanes)


@dataclass(frozen=True, slots=True)
class RealizationPlan:
    """Selected target plan before abstract physical lowering."""

    realizations: tuple[SelectedRealization, ...]
    deliveries: tuple[PlannedDelivery, ...]
    exact_lifetimes: tuple[ExactLifetime, ...]
    wire_sums: tuple[WireSumResource, ...]
    entity_cost: int
    transport_cost: int
    delay_buses: tuple[DelayBusResource, ...] = ()
    state_cells: tuple[SelectedStateCell, ...] = ()
    periodic_commit: PeriodicCommitResource | None = None

    @property
    def total_cost(self) -> int:
        return self.entity_cost + self.transport_cost

    def realization_for(self, operation: int) -> SelectedRealization:
        for realization in self.realizations:
            if realization.operation == operation:
                return realization
        raise KeyError(operation)

    def state_cell_for(self, register_name: str) -> SelectedStateCell:
        for cell in self.state_cells:
            if cell.register_name == register_name:
                return cell
        raise KeyError(register_name)

    def deliveries_from(self, producer: int) -> tuple[PlannedDelivery, ...]:
        return tuple(item for item in self.deliveries if item.producer == producer)

    def delay_bus_for(self, producer: int) -> DelayBusResource | None:
        for bus in self.delay_buses:
            if any(lane.producer == producer for lane in bus.lanes):
                return bus
        return None
