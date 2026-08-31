"""Clock-shape classification and reduction helpers for Milestone G acceptance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from factorio_circuit import Circuit
from factorio_circuit.analysis.state_timing import StateTimingPlan


@dataclass(frozen=True, slots=True)
class PeriodicFuzzSupport:
    """Whether the uniform-period periodic differential oracle applies to one timing plan."""

    supported: bool
    period: int | None
    reason: str | None = None


def classify_uniform_periodic_timing(plan: StateTimingPlan) -> PeriodicFuzzSupport:
    """Classify a timing plan before sending it to the G3 uniform-period comparator."""

    if plan.event_clocks:
        return PeriodicFuzzSupport(
            False,
            None,
            "Event clocks require the Event differential harness",
        )
    period = plan.uniform_period
    if period is None:
        return PeriodicFuzzSupport(
            False,
            None,
            "non-uniform periodic state domains require per-domain logical-to-physical mapping",
        )
    return PeriodicFuzzSupport(True, period)


def build_heterogeneous_periodic_circuit(*, connect_outputs: bool = False) -> Circuit:
    """Build the established independent period-1 / period-3 state-domain example."""

    circuit = Circuit("g8_heterogeneous_periodic")
    data = circuit.signals("data")
    fast = circuit.accumulator("fast")
    slow = circuit.freeze("slow")

    old_slow = slow.sample()
    fast.add(data)
    slow.set(data, when=old_slow.any())

    circuit.step(1)
    new_fast = fast.sample()
    new_slow = slow.sample()
    if connect_outputs:
        circuit.output("mixed", new_fast + new_slow)
    else:
        circuit.output("fast", new_fast)
        circuit.output("slow", new_slow)
    return circuit


@dataclass(frozen=True, slots=True)
class ClockStructureCase:
    """Serializable clock-topology dimensions understood by the G8 reducer."""

    seed: int
    stages: tuple[str, ...]

    def describe(self) -> str:
        return f"seed={self.seed} stages={self.stages!r}"


def shrink_clock_structure(
    case: ClockStructureCase,
    fails: Callable[[ClockStructureCase], bool],
) -> ClockStructureCase:
    """Greedily remove derived-clock stages while preserving a failure."""

    current = case
    changed = True
    while changed:
        changed = False
        for index in range(len(current.stages)):
            candidate = replace(
                current,
                stages=current.stages[:index] + current.stages[index + 1 :],
            )
            if fails(candidate):
                current = candidate
                changed = True
                break
    return current
