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
    """Compare one-physical-tick logical streams at each declared output phase."""

    _assert_stream_with_period(semantic, physical, input_stream, period=1)


def assert_same_periodic_stream(
    semantic: CircuitModule,
    physical: PhysicalCircuit,
    input_stream: list[dict[str, object]],
    *,
    period: int,
) -> None:
    """Compare logical streams whose state clock advances every ``period`` physical ticks.

    Each logical input row is held constant for one complete physical clock period.  Logical output
    tick ``n`` is compared at physical tick ``n * period + output.phase``.  This preserves the
    compiler's explicit distinction between logical state boundaries and Factorio combinator ticks.
    """

    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("period must be a positive integer")
    _assert_stream_with_period(semantic, physical, input_stream, period=period)


def _assert_stream_with_period(
    semantic: CircuitModule,
    physical: PhysicalCircuit,
    input_stream: list[dict[str, object]],
    *,
    period: int,
) -> None:
    physical_inputs = [dict(row) for row in input_stream for _ in range(period)]
    observations = simulate_stream(physical, physical_inputs)
    expected = simulate_semantic_stream(semantic, input_stream)
    for logical_tick, expected_tick in enumerate(expected):
        for output_index, port in enumerate(physical.outputs):
            physical_tick = logical_tick * period + port.phase
            actual = observations[physical_tick][output_index]
            wanted = expected_tick[output_index]
            if actual != wanted:
                raise AssertionError(
                    "stream mismatch: "
                    f"logical_tick={logical_tick}, physical_tick={physical_tick}, "
                    f"output={port.name}, phase={port.phase}, period={period}, "
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
