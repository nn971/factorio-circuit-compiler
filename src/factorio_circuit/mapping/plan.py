"""Selected realization plan produced by temporal technology mapping.

A plan is target-level enough to say which implementation candidate realizes each semantic recipe,
when its ports are used, which exact lifetimes need storage, and which explicit shared-resource
mechanisms were selected. It deliberately stops before concrete Factorio signal identities,
red/green wiring, placement, and routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DeliveryKind(StrEnum):
    """How one selected producer realization satisfies one semantic use."""

    REUSE = "reuse"
    OBSERVE_AT = "observe-at"
    PRIVATE_TRANSPORT = "private-transport"


@dataclass(frozen=True, slots=True)
class SelectedRealization:
    operation: int
    candidate: int
    output_phase: int
    entity_cost: int


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
        if self.kind is not DeliveryKind.PRIVATE_TRANSPORT:
            return 0
        if self.transport_start_phase is None:
            raise ValueError("private transport delivery has no start phase")
        return self.phase - self.transport_start_phase


@dataclass(frozen=True, slots=True)
class ExactLifetime:
    """One prefix-shareable exact token lifetime before any shared bus is considered."""

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
class RealizationPlan:
    """Complete first-milestone target plan before abstract physical lowering."""

    realizations: tuple[SelectedRealization, ...]
    deliveries: tuple[PlannedDelivery, ...]
    exact_lifetimes: tuple[ExactLifetime, ...]
    wire_sums: tuple[WireSumResource, ...]
    entity_cost: int
    transport_cost: int

    @property
    def total_cost(self) -> int:
        return self.entity_cost + self.transport_cost

    def realization_for(self, operation: int) -> SelectedRealization:
        for realization in self.realizations:
            if realization.operation == operation:
                return realization
        raise KeyError(operation)

    def deliveries_from(self, producer: int) -> tuple[PlannedDelivery, ...]:
        return tuple(item for item in self.deliveries if item.producer == producer)
