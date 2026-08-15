"""Target latency ownership shared by semantic timing and physical lowerers.

The Factorio target currently gives each combinator stage one game-tick of latency.  Keeping the
stage costs here prevents the semantic analyzer and the two lowerers from drifting apart while
leaving the target-specific policy in one small, replaceable model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TargetLatencyModel:
    """Physical stage costs used by the current Factorio target."""

    combinator_stage: int = 1
    state_commit_stage: int = 1
    select_data_stage: int = 3
    select_condition_stage: int = 2

    def __post_init__(self) -> None:
        for name in (
            "combinator_stage",
            "state_commit_stage",
            "select_data_stage",
            "select_condition_stage",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"target latency {name} must be a positive integer")

    def state_edge_latency(self, expression_latency: int) -> int:
        """Latency from a state source to the next transition boundary."""

        return expression_latency + self.state_transition_latency("commit")

    def operation_latency(self, family: str, operation: str | None = None) -> int:
        """Return the latency of one target operation family.

        The operation spelling is accepted even though the current Factorio target gives all
        supported combinator families one tick.  Keeping the dispatch here lets a target with
        different stage costs evolve without duplicating timing assumptions in analyzers/lowerers.
        """

        if family not in {
            "scalar_binary",
            "compare",
            "select_data",
            "select_condition",
            "vector_binary",
            "vector_scalar",
            "vector_filter",
            "vector_select",
        }:
            raise ValueError(f"unsupported target operation family {family!r}")
        del operation
        if family == "select_data":
            return self.select_data_stage
        if family == "select_condition":
            return self.select_condition_stage
        return self.combinator_stage

    def state_transition_latency(self, kind: str) -> int:
        """Return the target stage cost for a state boundary operation."""

        if kind not in {"commit", "capture", "set", "add", "clear"}:
            raise ValueError(f"unsupported target state transition kind {kind!r}")
        return self.state_commit_stage


FACTORIO_LATENCY = TargetLatencyModel()
