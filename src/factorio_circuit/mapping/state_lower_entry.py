"""Public entry point for periodic-state physical lowering.

The mature lowering stack may ask to align an operand to a phase it already occupies. Those calls
are semantically no-ops and must not consume a second planned delivery from the temporal mapping
plan. Keeping that guard in a tiny adapter makes the invariant explicit while the experimental
state lowerer still reuses the established arithmetic/vector emitters.
"""

from __future__ import annotations

from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector

from .plan import RealizationPlan
from .problem import MappingProblem
from .state_lower import PeriodicStatePhysicalLoweringResult, _MappedPeriodicStateLowerer
from .state_templates import StateCellCandidate
from .templates import ImplementationCandidate


class _NoOpSafeMappedPeriodicStateLowerer(_MappedPeriodicStateLowerer):
    """Do not let redundant same-phase alignment consume planned semantic uses."""

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase == target_phase:
            return value
        return super().delay_to(value, target_phase)

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase == target_phase:
            return value
        return super().delay_vector_to(value, target_phase)


def lower_periodic_state_mapping_plan(
    module: CircuitModule,
    problem: MappingProblem,
    candidates: tuple[ImplementationCandidate, ...],
    state_candidates: tuple[StateCellCandidate, ...],
    plan: RealizationPlan,
) -> PeriodicStatePhysicalLoweringResult:
    """Lower one mapped periodic plan with side-effect-free no-op alignment."""

    return _NoOpSafeMappedPeriodicStateLowerer(
        module,
        problem,
        candidates,
        state_candidates,
        plan,
    ).lower()


__all__ = ["PeriodicStatePhysicalLoweringResult", "lower_periodic_state_mapping_plan"]
