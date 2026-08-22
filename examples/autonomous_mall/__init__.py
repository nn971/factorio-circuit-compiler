"""Offline research scaffold for a future autonomous quality mall.

No circuit-side autonomous controller is currently accepted as the project architecture.
The retained modules cover real-data recipe extraction, canonical recipe DAG construction,
quality/recycling mechanics, and an exact offline material-efficiency oracle.
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

__all__ = [
    "AmbiguousProducerError",
    "Commodity",
    "InvalidRecipeOverrideError",
    "ItemRecipe",
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
    "solve_quality_policy",
]
