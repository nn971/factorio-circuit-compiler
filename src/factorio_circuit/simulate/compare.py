"""Behavior-comparison helpers for compiler/optimizer validation."""

from __future__ import annotations

import random

from factorio_circuit.ir.physical import PhysicalCircuit
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.simulate.physical import evaluate as evaluate_physical
from factorio_circuit.simulate.physical import simulate_stream
from factorio_circuit.simulate.semantic import evaluate as evaluate_semantic
from factorio_circuit.simulate.semantic import simulate_stream as simulate_semantic_stream
from factorio_circuit.target.factorio.semantics import i32


def assert_same_values(
    semantic: CircuitModule, physical: PhysicalCircuit, cases: list[dict[str, int]]
) -> None:
    for inputs in cases:
        expected = evaluate_semantic(semantic, inputs)
        actual = evaluate_physical(physical, inputs)
        if actual != expected:
            raise AssertionError(f"inputs={inputs}: semantic={expected}, physical={actual}")


def assert_same_stream(
    semantic: CircuitModule,
    physical: PhysicalCircuit,
    input_stream: list[dict[str, object]],
) -> None:
    """Compare logical stream values against physical outputs at each declared phase."""

    observations = simulate_stream(physical, input_stream)
    expected = simulate_semantic_stream(semantic, input_stream)
    for logical_tick, expected_tick in enumerate(expected):
        for output_index, port in enumerate(physical.outputs):
            physical_tick = logical_tick + port.phase
            actual = observations[physical_tick][output_index]
            wanted = expected_tick[output_index]
            if actual != wanted:
                raise AssertionError(
                    "stream mismatch: "
                    f"logical_tick={logical_tick}, output={port.name}, phase={port.phase}, "
                    f"expected={wanted}, actual={actual}"
                )


def assert_equivalent_random(
    semantic: CircuitModule,
    physical: PhysicalCircuit,
    *,
    cases: int = 64,
    seed: int = 0,
) -> None:
    """Generate a deterministic random input stream and compare tick-aware behavior."""

    rng = random.Random(seed)
    interesting = [0, 1, -1, 2, -2, 2**31 - 1, -(2**31)]
    stream: list[dict[str, object]] = []
    for _ in range(cases):
        item: dict[str, object] = {}
        for input_ in semantic.inputs:
            if rng.random() < 0.35:
                item[input_.name] = rng.choice(interesting)
            else:
                item[input_.name] = i32(rng.getrandbits(32))
        stream.append(item)
    assert_same_stream(semantic, physical, stream)
