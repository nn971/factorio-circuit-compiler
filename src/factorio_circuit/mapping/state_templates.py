"""Physical state-cell implementation candidates for periodic technology mapping.

State timing is target technology. The neutral recurrence IR records register occurrences and
semantic transitions only; candidates in this module own the physical read/write port timing and
entity cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.state import FreezeRegister

from .problem import MappingProblem, MappingProblemError
from .templates import CandidateOutputMode


@dataclass(frozen=True, slots=True)
class StateTransitionPortTiming:
    """Candidate-owned transition input offsets relative to the next state read phase."""

    transition: int
    value_phase_offset: int | None
    when_phase_offset: int | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.transition, bool)
            or not isinstance(self.transition, int)
            or self.transition <= 0
        ):
            raise MappingProblemError("state transition timing requires a positive transition id")
        for value in (self.value_phase_offset, self.when_phase_offset):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise MappingProblemError("state transition port offsets must be integers")
            if value is not None and value >= 0:
                raise MappingProblemError(
                    "first state-cell candidates require transition inputs before the next read"
                )


@dataclass(frozen=True, slots=True)
class StateCellCandidate:
    """One finite physical implementation alternative for a periodic state register."""

    id: int
    register_name: str
    name: str
    transition_ports: tuple[StateTransitionPortTiming, ...]
    entity_cost: int
    read_mode: CandidateOutputMode = CandidateOutputMode.STABLE

    def __post_init__(self) -> None:
        if isinstance(self.id, bool) or not isinstance(self.id, int) or self.id <= 0:
            raise MappingProblemError("state-cell candidate id must be a positive integer")
        if not self.register_name:
            raise MappingProblemError("state-cell candidate register name must be non-empty")
        if not self.name:
            raise MappingProblemError("state-cell candidate name must be non-empty")
        if not self.transition_ports:
            raise MappingProblemError("state-cell candidate must cover at least one transition")
        transition_ids = [item.transition for item in self.transition_ports]
        if len(set(transition_ids)) != len(transition_ids):
            raise MappingProblemError("state-cell candidate transition ids must be unique")
        if isinstance(self.entity_cost, bool) or not isinstance(self.entity_cost, int):
            raise MappingProblemError("state-cell candidate entity cost must be an integer")
        if self.entity_cost < 0:
            raise MappingProblemError("state-cell candidate entity cost must be non-negative")
        if self.read_mode is not CandidateOutputMode.STABLE:
            raise MappingProblemError("first state-cell solver supports stable read ports only")


def ordinary_freeze_state_candidate(
    problem: MappingProblem,
    register_name: str,
    *,
    candidate_id: int,
) -> StateCellCandidate:
    """Describe the current four-combinator Freeze register topology.

    The existing target topology has two one-tick control deciders (pass/hold), one transparent data
    gate, and one memory combinator. If logical occurrence ``k+1`` is visible at phase ``r``:

    - semantic update data is consumed at ``r-1`` by the transparent gate;
    - semantic ``when`` is consumed at ``r-2`` because pass/hold normalization takes one tick;
    - the memory output exposes the new state at ``r`` and remains stable until the next update.

    These offsets are properties of this candidate, not of ``MappingStateTransition``.
    """

    transitions = tuple(
        item for item in problem.state_transitions if item.register_name == register_name
    )
    if len(transitions) != 1:
        raise MappingProblemError(
            f"ordinary Freeze state cell {register_name!r} requires exactly one transition"
        )
    transition = transitions[0]
    if not isinstance(transition.semantic.register, FreezeRegister) or transition.kind != "set":
        raise MappingProblemError(
            f"ordinary Freeze state cell {register_name!r} requires one Freeze set transition"
        )
    if transition.value is None or transition.when is None:
        raise MappingProblemError("ordinary Freeze set requires both data and condition ports")

    return StateCellCandidate(
        id=candidate_id,
        register_name=register_name,
        name="ordinary Freeze cell",
        transition_ports=(
            StateTransitionPortTiming(
                transition=transition.id,
                value_phase_offset=-1,
                when_phase_offset=-2,
            ),
        ),
        entity_cost=4,
    )


def ordinary_freeze_state_candidates(problem: MappingProblem) -> tuple[StateCellCandidate, ...]:
    """Generate ordinary candidates for every Freeze register represented by the problem."""

    register_names = tuple(
        dict.fromkeys(
            [item.register_name for item in problem.state_reads]
            + [item.register_name for item in problem.state_transitions]
        )
    )
    result: list[StateCellCandidate] = []
    for register_name in register_names:
        transitions = tuple(
            item for item in problem.state_transitions if item.register_name == register_name
        )
        register = (
            transitions[0].semantic.register
            if transitions
            else next(
                item.semantic.register
                for item in problem.state_reads
                if item.register_name == register_name
            )
        )
        if not isinstance(register, FreezeRegister):
            continue
        result.append(
            ordinary_freeze_state_candidate(
                problem,
                register_name,
                candidate_id=len(result) + 1,
            )
        )
    return tuple(result)
