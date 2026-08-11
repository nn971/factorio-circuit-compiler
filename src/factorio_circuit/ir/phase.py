"""Phase-contract model reserved for upcoming milestones."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FreePhase:
    """No phase relation beyond physical feasibility."""


@dataclass(frozen=True, slots=True)
class AlignedPhase:
    group: str


@dataclass(frozen=True, slots=True)
class RelativePhase:
    reference: str
    shift: int


PhaseConstraint = FreePhase | AlignedPhase | RelativePhase
