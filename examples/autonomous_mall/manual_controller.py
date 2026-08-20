"""Manually wired transactional controller for the autonomous mall in-game prototype.

This is intentionally application-specific and lives under ``examples``. The economic LP planner
remains the oracle for choosing jobs; this circuit exercises the physical transaction layer that the
future autonomous planner will drive.

The controller allocates one conservative batch only when every worker is idle. Candidate jobs are
considered in ``DEFAULT_WORKERS`` order against one live roboport stock snapshot, so accepted jobs
atomically reserve their complete requester-chest demand before later candidates are considered.
No new batch starts until all accepted jobs have completed and all external completion latches have
cleared. This avoids treating robot flight or assembler-local inventory as fresh globally available
stock.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.compiler import CompilationResult

from .model import WorkerKind

MODE_START = SignalId("virtual", "signal-C")
MODE_WAIT = SignalId("virtual", "signal-W")
DISPATCH_SEEN = SignalId("virtual", "signal-D")


@dataclass(frozen=True, slots=True)
class ManualWorkerSpec:
    """One fixed physical worker in the manually wired prototype."""

    name: str
    kind: WorkerKind

    @property
    def uses_recipe_command(self) -> bool:
        return self.kind is not WorkerKind.RECYCLER


DEFAULT_WORKERS = (
    ManualWorkerSpec("p0", WorkerKind.PRODUCTIVITY),
    ManualWorkerSpec("p1", WorkerKind.PRODUCTIVITY),
    ManualWorkerSpec("q0", WorkerKind.QUALITY),
    ManualWorkerSpec("q1", WorkerKind.QUALITY),
    ManualWorkerSpec("r0", WorkerKind.RECYCLER),
)


@dataclass(slots=True)
class _WorkerState:
    spec: ManualWorkerSpec
    job_enable: object
    job_request: object
    job_recipe: object | None
    working: object
    finished: object
    mode: object
    held_request: object
    held_recipe: object | None


def build_manual_controller(
    workers: tuple[ManualWorkerSpec, ...] = DEFAULT_WORKERS,
) -> Circuit:
    """Build the manually wired multi-worker transactional controller.

    External ``*_finished`` inputs must be persistent completion latches, not one-tick machine
    pulses. The controller emits ``*_ack_finished`` while consuming a completion so the external
    latch can reset. ``dispatch`` is edge-armed: hold it high until the batch is accepted, then
    return it to zero before requesting another batch.
    """

    circuit = Circuit("autonomous_mall_manual_controller")
    stock = circuit.signals("stock")
    dispatch_active = circuit.input("dispatch") != 0

    start_token = circuit.constant_signals({MODE_START: 1})
    wait_token = circuit.constant_signals({MODE_WAIT: 1})
    dispatch_token = circuit.constant_signals({DISPATCH_SEEN: 1})

    dispatch_seen = circuit.freeze("dispatch_seen")
    old_dispatch_seen = dispatch_seen.sample()
    dispatch_is_seen = old_dispatch_seen.signal(DISPATCH_SEEN) != 0

    states: list[_WorkerState] = []
    for spec in workers:
        job_enable = circuit.input(f"{spec.name}_job_enable") != 0
        job_request = circuit.signals(f"{spec.name}_job_request")
        job_recipe = (
            circuit.signals(f"{spec.name}_job_recipe") if spec.uses_recipe_command else None
        )
        working = circuit.input(f"{spec.name}_working") != 0
        finished = circuit.input(f"{spec.name}_finished") != 0
        mode = circuit.freeze(f"{spec.name}_mode")
        held_request = circuit.freeze(f"{spec.name}_request")
        held_recipe = (
            circuit.freeze(f"{spec.name}_recipe") if spec.uses_recipe_command else None
        )
        states.append(
            _WorkerState(
                spec=spec,
                job_enable=job_enable,
                job_request=job_request,
                job_recipe=job_recipe,
                working=working,
                finished=finished,
                mode=mode,
                held_request=held_request,
                held_recipe=held_recipe,
            )
        )

    old_modes = {state.spec.name: state.mode.sample() for state in states}
    old_requests = {state.spec.name: state.held_request.sample() for state in states}
    old_recipes = {
        state.spec.name: state.held_recipe.sample()
        for state in states
        if state.held_recipe is not None
    }

    batch_ready = dispatch_is_seen.logical_not()
    for state in states:
        idle = old_modes[state.spec.name].any().logical_not()
        batch_ready = (
            batch_ready * idle * state.working.logical_not() * state.finished.logical_not()
        )

    dispatch_fire = dispatch_active * batch_ready
    rearm_dispatch = dispatch_is_seen * dispatch_active.logical_not()
    dispatch_change = dispatch_fire | rearm_dispatch
    dispatch_seen.set(dispatch_token.gate(dispatch_fire), when=dispatch_change)

    available = stock
    accepts: list[object] = []

    for state in states:
        mode = old_modes[state.spec.name]
        starting = mode.signal(MODE_START) != 0
        waiting = mode.signal(MODE_WAIT) != 0

        candidate = dispatch_fire * state.job_enable * state.job_request.any()
        if state.job_recipe is not None:
            candidate = candidate * state.job_recipe.any()

        request_missing = (state.job_request - available).positive().any()
        accept = candidate * request_missing.logical_not()
        accepts.append(accept)
        available = available - state.job_request.gate(accept)

        worker_started = starting * state.working
        worker_done = waiting * state.finished
        mode_change = accept | worker_started | worker_done
        next_mode = start_token.gate(accept) + wait_token.gate(worker_started)
        state.mode.set(next_mode, when=mode_change)

        state.held_request.set(state.job_request, when=accept)
        if state.held_recipe is not None and state.job_recipe is not None:
            state.held_recipe.set(state.job_recipe, when=accept)

        requester_demand = old_requests[state.spec.name].gate(starting)
        circuit.output(f"{state.spec.name}_requester_demand", requester_demand)
        if state.held_recipe is not None:
            circuit.output(
                f"{state.spec.name}_recipe",
                old_recipes[state.spec.name].gate(starting),
            )
        circuit.output(f"{state.spec.name}_accepted", accept)
        circuit.output(f"{state.spec.name}_busy", starting | waiting)
        circuit.output(f"{state.spec.name}_waiting_finished", waiting)
        circuit.output(f"{state.spec.name}_ack_finished", worker_done)

    any_accepted = accepts[0]
    for accept in accepts[1:]:
        any_accepted = any_accepted | accept

    circuit.output("batch_ready", batch_ready)
    circuit.output("dispatch_armed", dispatch_is_seen.logical_not())
    circuit.output("any_accepted", any_accepted)
    circuit.output("remaining_snapshot", available)
    return circuit


def _port_colors(result: CompilationResult, marker_entity: int) -> str:
    colors = {
        wire.color.value
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    return "/".join(sorted(colors)) or "unwired"


def _signal_name(signal: SignalId | None) -> str:
    return "VECTOR" if signal is None else f"{signal.kind}:{signal.name}"


def print_manual_wiring_map(result: CompilationResult) -> None:
    """Print concrete signal and wire assignments for hand-wiring the prototype."""

    print("=== AUTONOMOUS MALL MANUAL WIRING MAP ===", file=sys.stderr)
    print("Inputs:", file=sys.stderr)
    for port in result.physical_circuit.inputs:
        print(
            f"  {port.name:<28} {_signal_name(port.signal):<32} "
            f"wire={_port_colors(result, port.marker_entity)}",
            file=sys.stderr,
        )

    print("Outputs:", file=sys.stderr)
    for port in result.physical_circuit.outputs:
        print(
            f"  {port.name:<28} {_signal_name(port.signal):<32} "
            f"wire={_port_colors(result, port.marker_entity)} phase={port.phase}",
            file=sys.stderr,
        )

    print("\nBlueprint string follows on stdout.", file=sys.stderr)


def main() -> None:
    result = compile_circuit(build_manual_controller())
    print_manual_wiring_map(result)
    print(result.blueprint_string)


if __name__ == "__main__":
    main()
