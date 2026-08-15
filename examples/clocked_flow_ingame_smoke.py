"""Generate the focused in-game smoke circuit and its self-driving schedule source.

The compiled circuit intentionally combines the most timing-sensitive implemented semantics:

- Event ``.step(1)`` occurrence-prefix suppression;
- a target clock gated by a Level value sampled on the parent Event;
- strict-prior ``HoldInto``;
- right-closed ``SumInto``;
- aligned VALID materialization.

The companion raw blueprint drives the exact 240-tick schedule documented in
``docs/clocked-flow-merge-smoke.md``.  Its four output terminals inherit the synthesized wire color
of the matching compiled input, so the in-game setup is only four terminal-to-input wires plus
power for the driver.
"""

from __future__ import annotations

from factorio_circuit import Circuit, SignalId, compile_circuit
from factorio_circuit.compiler import CompilationResult
from factorio_circuit.ir.output import OutputMaterializationPolicy
from factorio_circuit.ir.physical import WireColor
from probes.blueprint_utils import (
    CIRCUIT_GREEN,
    CIRCUIT_RED,
    COMBINATOR_OUTPUT_GREEN,
    COMBINATOR_OUTPUT_RED,
    Blueprint,
    blueprint,
    constant_combinator,
    encode_blueprint,
    signal,
)

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def build_circuit() -> Circuit:
    circuit = Circuit("clocked_flow_ingame_smoke")
    enabled = circuit.input("enabled")
    source = circuit.signal_event("source", guaranteed_min_separation=4)
    tick = circuit.event("tick", guaranteed_min_separation=5)

    gated_tick = circuit.gate_clock(
        tick,
        when=circuit.sample_on(enabled, tick),
    )

    source_tail = source.step(1)
    held = circuit.hold_into(source, gated_tick)
    window = circuit.sum_into(source, gated_tick)

    circuit.output("tail", source_tail, policy=OutputMaterializationPolicy.VALID)
    circuit.output("held", held, policy=OutputMaterializationPolicy.VALID)
    circuit.output("window", window, policy=OutputMaterializationPolicy.VALID)
    return circuit


def _format_signal(value: object) -> str:
    kind = getattr(value, "kind", None)
    name = getattr(value, "name", None)
    if isinstance(kind, str) and isinstance(name, str):
        return f"{kind}:{name}"
    return repr(value)


def _input_color(compiled: CompilationResult, name: str) -> WireColor:
    port = next(port for port in compiled.physical_circuit.inputs if port.name == name)
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


def _arithmetic(
    entity_number: int,
    x: float,
    y: float,
    *,
    first: str,
    second_constant: int,
    operation: str,
    output: str,
    description: str,
) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "arithmetic-combinator",
        "position": {"x": x, "y": y},
        "direction": 4,
        "player_description": description,
        "control_behavior": {
            "arithmetic_conditions": {
                "first_signal": signal("virtual", first),
                "first_signal_networks": {"red": True, "green": False},
                "second_constant": second_constant,
                "operation": operation,
                "output_signal": signal("virtual", output),
            }
        },
    }


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
                        "first_signal": signal("virtual", "signal-P"),
                        "first_signal_networks": {"red": True, "green": False},
                        "comparator": "=",
                        "constant": phase,
                    }
                ],
                "outputs": [
                    {
                        "signal": signal(output_signal.kind, output_signal.name),
                        "copy_count_from_input": False,
                        "constant": output_constant,
                    }
                ],
            }
        },
    }


def build_driver_blueprint(compiled: CompilationResult) -> Blueprint:
    input_signals = compiled.physical_circuit.input_signals
    source_valid = input_signals["source__valid"]
    tick_valid = input_signals["tick__valid"]
    enabled = input_signals["enabled"]

    output_colors = {
        "source": _input_color(compiled, "source"),
        "source__valid": _input_color(compiled, "source__valid"),
        "tick__valid": _input_color(compiled, "tick__valid"),
        "enabled": _input_color(compiled, "enabled"),
    }

    entities: list[dict[str, object]] = [
        _arithmetic(
            1,
            -7,
            0,
            first="signal-C",
            second_constant=1,
            operation="+",
            output="signal-C",
            description="DRIVER COUNTER: C <- C + 1",
        ),
        _arithmetic(
            2,
            -4,
            0,
            first="signal-C",
            second_constant=240,
            operation="%",
            output="signal-P",
            description="DRIVER PHASE: P = C mod 240",
        ),
        _pulse_decider(
            3,
            -1,
            -7.5,
            phase=20,
            output_signal=IRON,
            output_constant=5,
            description="P=20: source iron-plate = 5",
        ),
        _pulse_decider(
            4,
            -1,
            -6,
            phase=80,
            output_signal=COPPER,
            output_constant=7,
            description="P=80: source copper-plate = 7",
        ),
        _pulse_decider(
            5,
            -1,
            -4.5,
            phase=140,
            output_signal=IRON,
            output_constant=3,
            description="P=140: source iron-plate = 3",
        ),
        _pulse_decider(
            6,
            -1,
            -3,
            phase=20,
            output_signal=source_valid,
            output_constant=1,
            description="P=20: source__valid = 1",
        ),
        _pulse_decider(
            7,
            -1,
            -1.5,
            phase=80,
            output_signal=source_valid,
            output_constant=1,
            description="P=80: source__valid = 1",
        ),
        _pulse_decider(
            8,
            -1,
            0,
            phase=140,
            output_signal=source_valid,
            output_constant=1,
            description="P=140: source__valid = 1",
        ),
        _pulse_decider(
            9,
            -1,
            1.5,
            phase=80,
            output_signal=tick_valid,
            output_constant=1,
            description="P=80: tick__valid = 1",
        ),
        _pulse_decider(
            10,
            -1,
            3,
            phase=120,
            output_signal=tick_valid,
            output_constant=1,
            description="P=120: tick__valid = 1 (gated away)",
        ),
        _pulse_decider(
            11,
            -1,
            4.5,
            phase=200,
            output_signal=tick_valid,
            output_constant=1,
            description="P=200: tick__valid = 1",
        ),
        _pulse_decider(
            12,
            -1,
            6,
            phase=80,
            output_signal=enabled,
            output_constant=1,
            description="P=80: enabled = 1",
        ),
        _pulse_decider(
            13,
            -1,
            7.5,
            phase=200,
            output_signal=enabled,
            output_constant=1,
            description="P=200: enabled = 1",
        ),
        constant_combinator(
            14,
            4,
            -6,
            [],
            description=(
                f"DRIVER OUTPUT source — use {output_colors['source'].value.upper()} wire "
                "to INPUT source"
            ),
        ),
        constant_combinator(
            15,
            4,
            -1.5,
            [],
            description=(
                "DRIVER OUTPUT source__valid — use "
                f"{output_colors['source__valid'].value.upper()} wire to INPUT source__valid"
            ),
        ),
        constant_combinator(
            16,
            4,
            3,
            [],
            description=(
                f"DRIVER OUTPUT tick__valid — use {output_colors['tick__valid'].value.upper()} "
                "wire to INPUT tick__valid"
            ),
        ),
        constant_combinator(
            17,
            4,
            6.75,
            [],
            description=(
                f"DRIVER OUTPUT enabled — use {output_colors['enabled'].value.upper()} wire "
                "to INPUT enabled"
            ),
        ),
        {
            "entity_number": 18,
            "name": "substation",
            "position": {"x": 1, "y": 0},
            "player_description": "POWER: connect this driver substation to your electric grid",
        },
    ]

    wires: list[list[int]] = [
        # Counter feedback and phase extraction.
        [1, COMBINATOR_OUTPUT_RED, 1, CIRCUIT_RED],
        [1, COMBINATOR_OUTPUT_RED, 2, CIRCUIT_RED],
        # One shared red phase bus over every pulse generator input.
        [2, COMBINATOR_OUTPUT_RED, 3, CIRCUIT_RED],
        *[[entity, CIRCUIT_RED, entity + 1, CIRCUIT_RED] for entity in range(3, 13)],
    ]

    terminal_groups = {
        "source": ((3, 4, 5), 14),
        "source__valid": ((6, 7, 8), 15),
        "tick__valid": ((9, 10, 11), 16),
        "enabled": ((12, 13), 17),
    }
    for name, (sources, terminal) in terminal_groups.items():
        output_connector, terminal_connector = _wire_connectors(output_colors[name])
        wires.extend(
            [source_entity, output_connector, terminal, terminal_connector]
            for source_entity in sources
        )

    return blueprint(
        "Clocked Flow merge smoke - schedule driver",
        (
            "Self-driving 240-tick environment for examples/clocked_flow_ingame_smoke.py. "
            "Wire the four labeled terminals to the matching compiled INPUT markers using the "
            "wire color written on each terminal. The scalar tick payload is intentionally unused."
        ),
        entities,
        wires,
    )


def main() -> None:
    compiled = compile_circuit(build_circuit())
    driver = build_driver_blueprint(compiled)

    print("# Scalar input signal allocation")
    for name, concrete_signal in compiled.physical_circuit.input_signals.items():
        print(f"{name}: {_format_signal(concrete_signal)}")

    print("# Driver terminal wiring")
    for name in ("source", "source__valid", "tick__valid", "enabled"):
        color = _input_color(compiled, name)
        print(f"{name}: {color.value.upper()} wire -> compiled INPUT {name}")
    print("tick: leave unwired; only tick__valid is used as the parent clock")

    print("# Scalar output signal allocation / physical phase")
    for port in compiled.physical_circuit.outputs:
        if port.signal is not None:
            print(f"{port.name}: {_format_signal(port.signal)} @ +{port.phase} tick(s)")
        else:
            print(f"{port.name}: vector @ +{port.phase} tick(s)")

    print("# Schedule-driver blueprint string")
    print(encode_blueprint(driver))
    print("# Compiled smoke-circuit blueprint string")
    print(compiled.blueprint_string)


if __name__ == "__main__":
    main()
