"""Physical state-cell implementation candidates for periodic technology mapping.

State timing is target technology. The neutral recurrence IR records register occurrences and
semantic
transitions only; candidates in this module own the physical read/write port timing and entity cost.
The ordinary periodic candidates consume one shared modulo-clock/startup-ready resource whose
predicates are folded directly into their multi-condition deciders.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.semantic import Constant
from factorio_circuit.ir.state import AccumulatorRegister, FreezeRegister

from .problem import MappingProblem, MappingProblemError
from .templates import CandidateOutputMode


@dataclass(frozen=True, slots=True)
class StateTransitionPortTiming:
    """Candidate-owned semantic transition input offsets relative to the next state read."""

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
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise MappingProblemError("state transition port offsets must be integers")
            if value is not None and value >= 0:
                raise MappingProblemError(
                    "state-cell transition inputs must precede the next state read"
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
    commit_phase_offset: int = -2

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
        if (
            isinstance(self.commit_phase_offset, bool)
            or not isinstance(self.commit_phase_offset, int)
            or self.commit_phase_offset >= 0
        ):
            raise MappingProblemError("periodic commit input must precede the next state read")


def ordinary_freeze_state_candidate(
    problem: MappingProblem,
    register_name: str,
    *,
    candidate_id: int,
) -> StateCellCandidate:
    """Describe the four-entity clocked Freeze topology.

    A shared periodic resource supplies ``ready`` plus the modulo-clock residue. Both predicates are
    folded into the same two pass/hold deciders already needed for Freeze control. If logical state
    ``S[k+1]`` is visible at phase ``r``:

    - semantic update data is consumed at ``r-1`` by the vector input gate;
    - semantic ``when`` and shared commit predicates are consumed at ``r-2``;
    - the memory network exposes the new state at ``r``.

    The four local entities are pass decider, hold decider, vector data gate, and memory combinator.
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
        name="ordinary clocked Freeze cell",
        transition_ports=(
            StateTransitionPortTiming(
                transition=transition.id,
                value_phase_offset=-1,
                when_phase_offset=-2,
            ),
        ),
        entity_cost=4,
        commit_phase_offset=-2,
    )


def ordinary_accumulator_state_candidate(
    problem: MappingProblem,
    register_name: str,
    *,
    candidate_id: int,
) -> StateCellCandidate:
    """Describe the four-entity clocked one-add/one-clear Accumulator used by Snake.

    Factorio deciders can combine several predicates directly. With the shared ``ready`` and clock
    residue available, one decider produces ``add active`` from
    ``ready && clock && add_when && !clear`` and one decider produces the complementary memory
    retain predicate. This removes the separate normalization/combination/delay chain used by the
    earlier unclocked prototype.

    Relative to the next state read at phase ``r``:

    - add data is consumed at ``r-1`` by the vector add gate;
    - add ``when``, clear ``when``, and shared commit predicates are consumed at ``r-2``;
    - the memory network exposes the new state at ``r``.

    Local entity cost is therefore four: add-active decider, retain decider, vector add gate,
    memory.
    """

    transitions = tuple(
        item for item in problem.state_transitions if item.register_name == register_name
    )
    adds = tuple(item for item in transitions if item.kind == "add")
    clears = tuple(item for item in transitions if item.kind == "clear")
    if len(adds) != 1 or len(clears) != 1 or len(transitions) != 2:
        raise MappingProblemError(
            f"ordinary Accumulator state cell {register_name!r} requires exactly one add "
            "and one clear"
        )
    add = adds[0]
    clear = clears[0]
    if not isinstance(add.semantic.register, AccumulatorRegister) or not isinstance(
        clear.semantic.register, AccumulatorRegister
    ):
        raise MappingProblemError(
            f"ordinary Accumulator state cell {register_name!r} requires Accumulator transitions"
        )
    if add.value is None or add.when is None:
        raise MappingProblemError("ordinary Accumulator add requires data and condition ports")
    if clear.value is not None or clear.when is None:
        raise MappingProblemError("ordinary Accumulator clear requires only a condition port")
    if isinstance(add.semantic.when, Constant):
        raise MappingProblemError(
            "first ordinary Accumulator candidate models a non-constant conditional add only"
        )

    return StateCellCandidate(
        id=candidate_id,
        register_name=register_name,
        name="ordinary clocked Accumulator add+clear cell",
        transition_ports=(
            StateTransitionPortTiming(
                transition=add.id,
                value_phase_offset=-1,
                when_phase_offset=-2,
            ),
            StateTransitionPortTiming(
                transition=clear.id,
                value_phase_offset=None,
                when_phase_offset=-2,
            ),
        ),
        entity_cost=4,
        commit_phase_offset=-2,
    )


def _state_register_names(problem: MappingProblem) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [item.register_name for item in problem.state_reads]
            + [item.register_name for item in problem.state_transitions]
        )
    )


def _register_for(problem: MappingProblem, register_name: str) -> object:
    transitions = tuple(
        item for item in problem.state_transitions if item.register_name == register_name
    )
    if transitions:
        return transitions[0].semantic.register
    return next(
        item.semantic.register
        for item in problem.state_reads
        if item.register_name == register_name
    )


def ordinary_freeze_state_candidates(problem: MappingProblem) -> tuple[StateCellCandidate, ...]:
    """Generate ordinary clocked candidates for every Freeze register in the problem."""

    result: list[StateCellCandidate] = []
    for register_name in _state_register_names(problem):
        if not isinstance(_register_for(problem, register_name), FreezeRegister):
            continue
        result.append(
            ordinary_freeze_state_candidate(
                problem,
                register_name,
                candidate_id=len(result) + 1,
            )
        )
    return tuple(result)


def ordinary_accumulator_state_candidates(
    problem: MappingProblem,
) -> tuple[StateCellCandidate, ...]:
    """Generate clocked candidates for supported Accumulator registers."""

    result: list[StateCellCandidate] = []
    for register_name in _state_register_names(problem):
        if not isinstance(_register_for(problem, register_name), AccumulatorRegister):
            continue
        result.append(
            ordinary_accumulator_state_candidate(
                problem,
                register_name,
                candidate_id=len(result) + 1,
            )
        )
    return tuple(result)


def ordinary_state_candidates(problem: MappingProblem) -> tuple[StateCellCandidate, ...]:
    """Generate one ordinary clocked state-cell implementation per supported register."""

    result: list[StateCellCandidate] = []
    for register_name in _state_register_names(problem):
        register = _register_for(problem, register_name)
        candidate_id = len(result) + 1
        if isinstance(register, FreezeRegister):
            candidate = ordinary_freeze_state_candidate(
                problem,
                register_name,
                candidate_id=candidate_id,
            )
        elif isinstance(register, AccumulatorRegister):
            candidate = ordinary_accumulator_state_candidate(
                problem,
                register_name,
                candidate_id=candidate_id,
            )
        else:  # pragma: no cover - canonical periodic state currently has only these families
            raise MappingProblemError(
                f"no ordinary state-cell implementation for register {register_name!r}"
            )
        result.append(candidate)
    return tuple(result)
