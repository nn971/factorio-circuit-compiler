"""Generate a tiny Factorio Random Input selector acceptance probe.

The probe exposes one whole-vector input named ``candidates`` and one whole-vector output named
``choice``. A compiler-owned ``RandomSignalOracleProvider`` consumes the candidates network and
materializes one selector combinator in Random Input mode. Import the generated blueprint, wire a
constant combinator carrying several nonzero signals to INPUT candidates using the printed color,
and hover the OUTPUT choice network to watch the selected signal change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from factorio_circuit import Circuit, CompilationResult, RandomSignalOracleProvider
from factorio_circuit.ir.physical import SelectorCombinator, WireColor
from factorio_circuit.synthesis.placement import PlacementOptions

_ORACLE = "choice"
_PROVIDER_INPUT = "candidates"


def _marker_wire_color(result: CompilationResult, marker_entity: int) -> WireColor:
    colors = {
        wire.color
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        rendered = ", ".join(sorted(color.value for color in colors)) or "none"
        raise ValueError(
            f"expected exactly one synthesized wire color at marker {marker_entity}; found {rendered}"
        )
    return next(iter(colors))


def build_probe(*, update_interval: int = 1) -> CompilationResult:
    circuit = Circuit("random_input_selector_probe")
    candidates = circuit.signals(_PROVIDER_INPUT)
    choice = circuit.oracle_signals(_ORACLE)
    circuit.bind_oracle_input(choice, _PROVIDER_INPUT, candidates)
    circuit.output(_ORACLE, choice)
    return circuit.compile(
        optimize=False,
        placement=PlacementOptions(strategy="row", restarts=1),
        oracle_providers={
            _ORACLE: RandomSignalOracleProvider(
                input_name=_PROVIDER_INPUT,
                update_interval=update_interval,
            )
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-interval",
        type=int,
        default=1,
        help="Random Input update interval in game ticks (default: 1)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("random-input-probe-blueprint.txt"),
        help="output blueprint file (default: random-input-probe-blueprint.txt)",
    )
    args = parser.parse_args()

    result = build_probe(update_interval=args.update_interval)
    selectors = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, SelectorCombinator)
    ]
    if len(selectors) != 1:
        raise ValueError(f"expected exactly one physical selector, found {len(selectors)}")
    selector = selectors[0]
    if selector.operation != "random" or selector.random_update_interval != args.update_interval:
        raise ValueError("Random Input selector configuration did not survive physical synthesis")

    candidates_port = next(
        port for port in result.physical_circuit.inputs if port.name == _PROVIDER_INPUT
    )
    choice_port = next(port for port in result.physical_circuit.outputs if port.name == _ORACLE)
    candidates_color = _marker_wire_color(result, candidates_port.marker_entity)
    choice_color = _marker_wire_color(result, choice_port.marker_entity)

    args.output.write_text(result.blueprint_string + "\n", encoding="utf-8")
    print(
        "random selector probe: "
        f"update_interval={args.update_interval}, combinators={result.physical_circuit.combinator_count}",
        file=sys.stderr,
    )
    print(
        "wire a constant-combinator candidate vector -> INPUT candidates with "
        f"{candidates_color.value.upper()}; observe OUTPUT choice with {choice_color.value.upper()}",
        file=sys.stderr,
    )
    print(f"wrote Random Input probe blueprint to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
