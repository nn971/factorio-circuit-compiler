"""Self-driving Factorio schedule harness for the clock-aware examples.

Each example prints two blueprints: a compiled circuit and a tiny raw driver. The driver repeats a
fixed schedule and exposes one labeled terminal per used compiler input. Wire each driver terminal
to the matching compiled INPUT marker using the wire color printed by the script.
"""

from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Mapping
from typing import Any

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.compiler import CompilationResult
from factorio_circuit.ir.physical import WireColor

FACTORIO_BLUEPRINT_VERSION = 562949955518464
CIRCUIT_RED = 1
CIRCUIT_GREEN = 2
COMBINATOR_OUTPUT_RED = 3
COMBINATOR_OUTPUT_GREEN = 4
DRIVER_ROW_SPACING = 7
POWER_SPACING = 16

DriverValue = int | Mapping[SignalId, int]
DriverSchedule = Mapping[str, Mapping[int, DriverValue]]
Blueprint = dict[str, Any]


def _signal(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _virtual(name: str) -> dict[str, str]:
    return {"type": "virtual", "name": name}


def _input_port(compiled: CompilationResult, name: str):
    try:
        return next(port for port in compiled.physical_circuit.inputs if port.name == name)
    except StopIteration as exc:
        raise ValueError(f"compiled circuit has no input {name!r}") from exc


def input_color(compiled: CompilationResult, name: str) -> WireColor:
    """Return the unique wire color used by one synthesized input marker."""

    port = _input_port(compiled, name)
    colors = {
        wire.color
        for wire in compiled.layout.wires
        if wire.source_entity == port.marker_entity or wire.target_entity == port.marker_entity
    }
    if len(colors) != 1:
        rendered = ", ".join(sorted(color.value for color in colors)) or "none"
        raise ValueError(f"expected one physical wire color for input {name!r}; found {rendered}")
    return next(iter(colors))


def _wire_connectors(color: WireColor) -> tuple[int, int]:
    if color is WireColor.RED:
        return COMBINATOR_OUTPUT_RED, CIRCUIT_RED
    return COMBINATOR_OUTPUT_GREEN, CIRCUIT_GREEN


def _pulse_decider(
    entity_number: int,
    x: float,
    y: float,
    *,
    phase: int,
    output_signal: SignalId,
    output_constant: int,
    description: str,
) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "decider-combinator",
        "position": {"x": x, "y": y},
        "direction": 4,
        "player_description": description,
        "control_behavior": {
            "decider_conditions": {
                "conditions": [
                    {
                        "first_signal": _virtual("signal-P"),
                        "first_signal_networks": {"red": True, "green": False},
                        "comparator": "=",
                        "constant": phase,
                    }
                ],
                "outputs": [
                    {
                        "signal": _signal(output_signal),
                        "copy_count_from_input": False,
                        "constant": output_constant,
                    }
                ],
            }
        },
    }


def _counter(entity_number: int) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "arithmetic-combinator",
        "position": {"x": -8, "y": 0},
        "direction": 4,
        "player_description": "CLOCKED EXAMPLE DRIVER: C <- C + 1",
        "control_behavior": {
            "arithmetic_conditions": {
                "first_signal": _virtual("signal-C"),
                "first_signal_networks": {"red": True, "green": False},
                "second_constant": 1,
                "operation": "+",
                "output_signal": _virtual("signal-C"),
            }
        },
    }


def _phase(entity_number: int, period: int) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "arithmetic-combinator",
        "position": {"x": -5, "y": 0},
        "direction": 4,
        "player_description": f"CLOCKED EXAMPLE DRIVER: P = C mod {period}",
        "control_behavior": {
            "arithmetic_conditions": {
                "first_signal": _virtual("signal-C"),
                "first_signal_networks": {"red": True, "green": False},
                "second_constant": period,
                "operation": "%",
                "output_signal": _virtual("signal-P"),
            }
        },
    }


def _terminal(entity_number: int, x: float, y: float, description: str) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "constant-combinator",
        "position": {"x": x, "y": y},
        "player_description": description,
    }


def _substation(entity_number: int, y: float) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "substation",
        "position": {"x": 1, "y": y},
        "player_description": "POWER SPINE: connect any substation to your electric grid",
    }


def _encode_blueprint(payload: Blueprint) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "0" + base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")


def build_driver_blueprint(
    compiled: CompilationResult,
    schedule: DriverSchedule,
    *,
    period: int,
    label: str,
) -> Blueprint:
    """Build a repeating deterministic driver for selected compiled input ports."""

    if period < 2:
        raise ValueError("driver period must be at least 2 ticks")

    entities: list[dict[str, object]] = [_counter(1), _phase(2, period)]
    wires: list[list[int]] = [
        [1, COMBINATOR_OUTPUT_RED, 1, CIRCUIT_RED],
        [1, COMBINATOR_OUTPUT_RED, 2, CIRCUIT_RED],
    ]
    pulse_entities: list[int] = []
    terminal_groups: list[tuple[str, list[int], int]] = []
    next_entity = 3

    for row_index, (input_name, occurrences) in enumerate(schedule.items()):
        color = input_color(compiled, input_name)
        port = _input_port(compiled, input_name)
        sources: list[int] = []
        base_y = row_index * DRIVER_ROW_SPACING

        for phase, payload in sorted(occurrences.items()):
            if not 0 <= phase < period:
                raise ValueError(f"driver phase {phase} is outside [0, {period})")
            outputs: list[tuple[SignalId, int]] = []
            if isinstance(payload, int):
                if payload != 0:
                    if port.signal is None:
                        raise ValueError(
                            f"scalar schedule for vector input {input_name!r} has no concrete signal"
                        )
                    outputs.append((port.signal, payload))
            else:
                outputs.extend((signal, count) for signal, count in payload.items() if count != 0)

            for output_signal, output_constant in outputs:
                entity = next_entity
                next_entity += 1
                entities.append(
                    _pulse_decider(
                        entity,
                        -1,
                        base_y + len(sources) * 1.5,
                        phase=phase,
                        output_signal=output_signal,
                        output_constant=output_constant,
                        description=(
                            f"P={phase}: {input_name} emits "
                            f"{output_signal.kind}:{output_signal.name}={output_constant}"
                        ),
                    )
                )
                pulse_entities.append(entity)
                sources.append(entity)

        terminal = next_entity
        next_entity += 1
        entities.append(
            _terminal(
                terminal,
                4,
                base_y,
                (
                    f"DRIVER OUTPUT {input_name} — use {color.value.upper()} wire to compiled "
                    f"INPUT {input_name}"
                ),
            )
        )
        terminal_groups.append((input_name, sources, terminal))

    max_y = max(0, (len(schedule) - 1) * DRIVER_ROW_SPACING + 5)
    power_y = 0
    while power_y <= max_y:
        entities.append(_substation(next_entity, power_y))
        next_entity += 1
        power_y += POWER_SPACING

    if pulse_entities:
        wires.append([2, COMBINATOR_OUTPUT_RED, pulse_entities[0], CIRCUIT_RED])
        wires.extend(
            [left, CIRCUIT_RED, right, CIRCUIT_RED]
            for left, right in zip(pulse_entities, pulse_entities[1:], strict=False)
        )

    for input_name, sources, terminal in terminal_groups:
        output_connector, terminal_connector = _wire_connectors(input_color(compiled, input_name))
        wires.extend(
            [source, output_connector, terminal, terminal_connector] for source in sources
        )

    return {
        "blueprint": {
            "item": "blueprint",
            "label": label,
            "description": (
                "Self-driving repeating schedule for a clock-aware compiler example. Wire every "
                "labeled DRIVER OUTPUT terminal to the matching compiled INPUT marker."
            ),
            "version": FACTORIO_BLUEPRINT_VERSION,
            "entities": entities,
            "wires": wires,
        }
    }


def emit_in_game_example(
    circuit: Circuit,
    schedule: DriverSchedule,
    *,
    period: int,
    title: str,
    expected: tuple[str, ...],
) -> None:
    """Compile one example and print wiring instructions plus driver/circuit blueprints."""

    compiled = compile_circuit(circuit)
    driver = build_driver_blueprint(compiled, schedule, period=period, label=f"{title} driver")

    print(f"# {title}")
    print("# Wire the driver terminals to the matching compiled INPUT markers:")
    for name in schedule:
        print(f"#   {name}: {input_color(compiled, name).value.upper()} wire")
    print("# Expected repeating behavior:")
    for line in expected:
        print(f"#   {line}")
    print("# Driver blueprint")
    print(_encode_blueprint(driver))
    print("# Compiled circuit blueprint")
    print(compiled.blueprint_string)
