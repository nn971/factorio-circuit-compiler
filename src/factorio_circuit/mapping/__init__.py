"""Joint temporal technology-mapping primitives."""

from .clocked_state_solver import (
    solve_periodic_state_bus_mapping_problem,
    solve_periodic_state_mapping_problem,
)
from .decider_cover import (
    DeciderConditionCover,
    add_decider_condition_cover_candidates,
    find_decider_condition_covers,
    flatten_decider_condition_cover,
)
from .extract import (
    build_periodic_level_mapping_problem,
    build_periodic_state_mapping_problem,
    build_stateless_level_mapping_problem,
)
from .lower import lower_stateless_mapping_plan
from .plan import (
    DelayBusLane,
    DelayBusResource,
    DeliveryKind,
    ExactLifetime,
    PeriodicCommitResource,
    PlannedDelivery,
    RealizationPlan,
    SelectedRealization,
    SelectedStateCell,
    WireSumResource,
)
from .problem import (
    MappingOperation,
    MappingProblem,
    MappingProblemError,
    MappingSink,
    MappingSource,
    MappingSourceMode,
    MappingStateRead,
    MappingStateTransition,
    MappingUse,
)
from .solver import MappingOptimizationResult, solve_mapping_problem
from .state_lower_entry import (
    PeriodicStatePhysicalLoweringResult,
    lower_periodic_state_mapping_plan,
)
from .state_solver import PeriodicStateMappingOptimizationResult
from .state_templates import (
    StateCellCandidate,
    StateTransitionPortTiming,
    ordinary_accumulator_state_candidate,
    ordinary_accumulator_state_candidates,
    ordinary_freeze_state_candidate,
    ordinary_freeze_state_candidates,
    ordinary_state_candidates,
)
from .state_validate import (
    validate_periodic_state_bus_plan,
    validate_periodic_state_plan,
)
from .templates import (
    CandidateOutputMode,
    ImplementationCandidate,
    ImplementationKind,
    ImplementationRecipe,
    add_select_constant_candidates,
    add_wire_sum_candidates,
    ordinary_candidate,
    ordinary_candidates,
    select_constant_candidate,
    wire_sum_candidate,
)
from .validate import validate_realization_plan

__all__ = [
    "CandidateOutputMode",
    "DeciderConditionCover",
    "DelayBusLane",
    "DelayBusResource",
    "DeliveryKind",
    "ExactLifetime",
    "ImplementationCandidate",
    "ImplementationKind",
    "ImplementationRecipe",
    "MappingOperation",
    "MappingOptimizationResult",
    "MappingProblem",
    "MappingProblemError",
    "MappingSink",
    "MappingSource",
    "MappingSourceMode",
    "MappingStateRead",
    "MappingStateTransition",
    "MappingUse",
    "PeriodicCommitResource",
    "PeriodicStateMappingOptimizationResult",
    "PeriodicStatePhysicalLoweringResult",
    "PlannedDelivery",
    "RealizationPlan",
    "SelectedRealization",
    "SelectedStateCell",
    "StateCellCandidate",
    "StateTransitionPortTiming",
    "WireSumResource",
    "add_decider_condition_cover_candidates",
    "add_select_constant_candidates",
    "add_wire_sum_candidates",
    "build_periodic_level_mapping_problem",
    "build_periodic_state_mapping_problem",
    "build_stateless_level_mapping_problem",
    "find_decider_condition_covers",
    "flatten_decider_condition_cover",
    "lower_periodic_state_mapping_plan",
    "lower_stateless_mapping_plan",
    "ordinary_accumulator_state_candidate",
    "ordinary_accumulator_state_candidates",
    "ordinary_candidate",
    "ordinary_candidates",
    "ordinary_freeze_state_candidate",
    "ordinary_freeze_state_candidates",
    "ordinary_state_candidates",
    "select_constant_candidate",
    "solve_mapping_problem",
    "solve_periodic_state_bus_mapping_problem",
    "solve_periodic_state_mapping_problem",
    "validate_periodic_state_bus_plan",
    "validate_periodic_state_plan",
    "validate_realization_plan",
    "wire_sum_candidate",
]
