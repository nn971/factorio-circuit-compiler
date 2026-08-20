"""Manually wired modular transaction cells for the autonomous mall prototype.

The original monolithic five-worker controller was a useful semantic model but it forced too many
runtime-open vector nets through shared synthesized connectors. Factorio has only red and green wire,
so the physical synthesizer correctly rejected that topology.

The in-game prototype is therefore intentionally modular:

* one stock snapshot cell tracks roboport inventory while ``dispatch`` is low and freezes it while
  ``dispatch`` is high;
* one reservation cell is pasted five times and chained p0 -> p1 -> q0 -> q1 -> r0;
* one assembler-worker cell is pasted four times for p0/p1/q0/q1;
* one recycler-worker cell is used for r0.

``dispatch`` freezes the stock and enables the reservation chain. After the chain settles, ``launch``
starts the accepted workers. Both signals stay high until the batch has completed, then return low to
re-arm the next batch. This two-phase manual handshake is deliberate for the first physical test: it
avoids cross-blueprint clock assumptions while preserving atomic reservation semantics.
"""

from __future__ import annotations

import base64
import json
import sys
import zlib
from collections.abc import Iterable

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.compiler import CompilationResult

MODE_START = SignalId("virtual", "signal-C")
MODE_WAIT = SignalId("virtual", "signal-W")
SEEN = SignalId("virtual", "signal-S")
FACTORIO_BLUEPRINT_VERSION = 562949955518464


def build_stock_snapshot() -> Circuit:
    """Track live stock while idle and freeze one scheduling snapshot during dispatch."""

    circuit = Circuit("autonomous_mall_stock_snapshot")
    stock = circuit.signals("stock")
    dispatch = circuit.input("dispatch") != 0

    snapshot = circuit.freeze("snapshot")
    old_snapshot = snapshot.sample()
    snapshot.set(stock, when=dispatch.logical_not())

    circuit.output("snapshot", old_snapshot)
    circuit.output("frozen", dispatch)
    return circuit


def build_reservation_cell() -> Circuit:
    """Reserve one candidate request against an upstream frozen availability vector."""

    circuit = Circuit("autonomous_mall_reservation_cell")
    active = circuit.input("active") != 0
    enabled = circuit.input("job_enable") != 0
    available = circuit.signals("available")
    request = circuit.signals("job_request")

    missing = (request - available).positive().any()
    accepted = active * enabled * request.any() * missing.logical_not()
    remaining = available - request.gate(accepted)

    circuit.output("accepted", accepted)
    circuit.output("remaining", remaining)
    return circuit


def build_worker_cell(*, recipe_command: bool) -> Circuit:
    """Execute one accepted physical job exactly once during a launch phase."""

    name = "autonomous_mall_assembler_worker" if recipe_command else "autonomous_mall_recycler_worker"
    circuit = Circuit(name)

    accepted = circuit.input("accepted") != 0
    launch = circuit.input("launch") != 0
    working = circuit.input("working") != 0
    finished = circuit.input("finished") != 0
    job_request = circuit.signals("job_request")
    job_recipe = circuit.signals("job_recipe") if recipe_command else None

    start_token = circuit.constant_signals({MODE_START: 1})
    wait_token = circuit.constant_signals({MODE_WAIT: 1})
    seen_token = circuit.constant_signals({SEEN: 1})

    mode = circuit.freeze("mode")
    seen = circuit.freeze("seen")
    held_request = circuit.freeze("held_request")
    held_recipe = circuit.freeze("held_recipe") if recipe_command else None

    old_mode = mode.sample()
    old_seen = seen.sample()
    old_request = held_request.sample()
    old_recipe = held_recipe.sample() if held_recipe is not None else None

    starting = old_mode.signal(MODE_START) != 0
    waiting = old_mode.signal(MODE_WAIT) != 0
    idle = old_mode.any().logical_not()
    already_seen = old_seen.signal(SEEN) != 0

    start = launch * accepted * idle * already_seen.logical_not()
    clear_seen = already_seen * accepted.logical_not()
    seen_change = start | clear_seen
    seen.set(seen_token.gate(start), when=seen_change)

    worker_started = starting * working
    worker_done = waiting * finished
    mode_change = start | worker_started | worker_done
    next_mode = start_token.gate(start) + wait_token.gate(worker_started)
    mode.set(next_mode, when=mode_change)

    held_request.set(job_request, when=start)
    if held_recipe is not None and job_recipe is not None:
        held_recipe.set(job_recipe, when=start)

    circuit.output("requester_demand", old_request.gate(starting))
    circuit.output("input_enable", starting)
    if old_recipe is not None:
        circuit.output("recipe", old_recipe)
    circuit.output("busy", starting | waiting)
    circuit.output("waiting_finished", waiting)
    circuit.output("ack_finished", worker_done)
    circuit.output("armed", already_seen.logical_not())
    return circuit


def build_assembler_worker() -> Circuit:
    return build_worker_cell(recipe_command=True)


def build_recycler_worker() -> Circuit:
    return build_worker_cell(recipe_command=False)


def compile_manual_cells() -> tuple[tuple[str, CompilationResult], ...]:
    """Compile the four reusable templates used by the manually wired five-worker mall."""

    return (
        ("stock snapshot", compile_circuit(build_stock_snapshot())),
        ("reservation cell - paste 5 copies", compile_circuit(build_reservation_cell())),
        ("assembler worker - paste 4 copies", compile_circuit(build_assembler_worker())),
        ("recycler worker", compile_circuit(build_recycler_worker())),
    )


def _port_colors(result: CompilationResult, marker_entity: int) -> str:
    colors = {
        wire.color.value
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    return "/".join(sorted(colors)) or "unwired"


def _signal_name(signal: SignalId | None) -> str:
    return "VECTOR" if signal is None else f"{signal.kind}:{signal.name}"


def print_manual_wiring_map(label: str, result: CompilationResult) -> None:
    """Print one compiled template's concrete signals and wire colors."""

    print(f"=== {label.upper()} ===", file=sys.stderr)
    print("Inputs:", file=sys.stderr)
    for port in result.physical_circuit.inputs:
        print(
            f"  {port.name:<20} {_signal_name(port.signal):<32} "
            f"wire={_port_colors(result, port.marker_entity)}",
            file=sys.stderr,
        )
    print("Outputs:", file=sys.stderr)
    for port in result.physical_circuit.outputs:
        print(
            f"  {port.name:<20} {_signal_name(port.signal):<32} "
            f"wire={_port_colors(result, port.marker_entity)} phase={port.phase}",
            file=sys.stderr,
        )
    print(file=sys.stderr)


def _blueprint_book(results: Iterable[tuple[str, CompilationResult]]) -> str:
    entries: list[dict[str, object]] = []
    for index, (label, result) in enumerate(results):
        blueprint = dict(result.blueprint_json["blueprint"])
        blueprint["label"] = label
        entries.append({"index": index, "blueprint": blueprint})

    payload = {
        "blueprint_book": {
            "item": "blueprint-book",
            "label": "Autonomous mall manual cells",
            "active_index": 0,
            "version": FACTORIO_BLUEPRINT_VERSION,
            "blueprints": entries,
        }
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "0" + base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")


def main() -> None:
    results = compile_manual_cells()
    for label, result in results:
        print_manual_wiring_map(label, result)
    print("Blueprint book string follows on stdout.", file=sys.stderr)
    print(_blueprint_book(results))


if __name__ == "__main__":
    main()
