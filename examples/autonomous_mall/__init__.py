"""Offline research scaffold for autonomous-mall policy experiments.

The package retains the earlier quality/recycling oracle and now also contains a deliberately small
reference scheduler for the deterministic, quality-free multi-worker milestone. Circuit-side
realization remains a separate design step.
"""

from .model import Commodity, Quality
from .quality_policy import QualityPlan, QualityPlanStep, QualityPolicyError, solve_quality_policy
from .quality_policy_graph import (
    ModuleProfile,
    QualityAction,
    QualityActionGraph,
    QualityActionKind,
    QualityPolicyConfig,
    build_quality_action_graph,
)
from .recipe_graph import (
    AmbiguousProducerError,
    InvalidRecipeOverrideError,
    ItemRecipe,
    MissingProducerError,
    RecipeCatalog,
    RecipeCycleError,
    RecipeDAG,
    RecipeGraphError,
    build_recipe_dag,
)
from .scheduler import DeterministicMallScheduler, DispatchPlan, MallJob, complete_jobs

__all__ = [
    "AmbiguousProducerError",
    "Commodity",
    "DeterministicMallScheduler",
    "DispatchPlan",
    "InvalidRecipeOverrideError",
    "ItemRecipe",
    "MallJob",
    "MissingProducerError",
    "ModuleProfile",
    "Quality",
    "QualityAction",
    "QualityActionGraph",
    "QualityActionKind",
    "QualityPlan",
    "QualityPlanStep",
    "QualityPolicyConfig",
    "QualityPolicyError",
    "RecipeCatalog",
    "RecipeCycleError",
    "RecipeDAG",
    "RecipeGraphError",
    "build_quality_action_graph",
    "build_recipe_dag",
    "complete_jobs",
    "solve_quality_policy",
]
