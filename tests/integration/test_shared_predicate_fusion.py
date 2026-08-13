from __future__ import annotations

import base64
import json
import zlib

from examples.sorting_network import build_sorting_circuit
from factorio_circuit import Circuit, compile_abstract_circuit, compile_circuit
from factorio_circuit.ir.abstract_physical import DeciderCombinator
from factorio_circuit.simulate.compare import assert_same_values


def _minmax() -> Circuit:
    circuit = Circuit("minmax")
    left = circuit.input("left")
    right = circuit.input("right")
    swap = left > right
    circuit.output("lo", swap.select(right, left))
    circuit.output("hi", swap.select(left, right))
    return circuit


def test_terminal_shared_predicate_fuses_into_two_multi_output_deciders() -> None:
    result = compile_abstract_circuit(_minmax())

    deciders = [
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, DeciderCombinator)
    ]
    assert result.abstract_physical.combinator_count == 2
    assert result.physical_circuit.combinator_count == 2
    assert len(deciders) == 2
    assert all(len(entity.additional_outputs) == 1 for entity in deciders)
    assert result.physical_circuit.output_phases == (1, 1)
    assert_same_values(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"left": 7, "right": 3},
            {"left": 2, "right": 9},
            {"left": 0, "right": 0},
            {"left": -5, "right": 4},
            {"left": 8, "right": -11},
        ],
    )


def test_sort8_shared_predicates_inline_without_losing_single_lane_chaining() -> None:
    result = compile_abstract_circuit(build_sorting_circuit(3))

    assert result.physical_circuit.combinator_count < 100
    assert max(result.physical_circuit.output_phases) == 6
    assert_same_values(
        result.semantic_ir,
        result.physical_circuit,
        [
            {f"x{i}": 7 - i for i in range(8)},
            {f"x{i}": (-1) ** i * (i + 1) for i in range(8)},
            {f"x{i}": 0 if i % 3 == 0 else i - 4 for i in range(8)},
        ],
    )


def test_sort8_real_blueprint_serializes_multi_output_deciders() -> None:
    result = compile_circuit(build_sorting_circuit(3))
    assert result.blueprint_string.startswith("0")
    decoded = json.loads(zlib.decompress(base64.b64decode(result.blueprint_string[1:])))
    deciders = [
        entity
        for entity in decoded["blueprint"]["entities"]
        if entity["name"] == "decider-combinator"
    ]
    assert any(
        len(entity["control_behavior"]["decider_conditions"]["outputs"]) > 1 for entity in deciders
    )
