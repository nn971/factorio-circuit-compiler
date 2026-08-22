"""Deterministic reference simulation with explicitly scripted oracle traces."""

from __future__ import annotations

from collections.abc import Mapping

from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.oracle import oracle_names
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.simulate.semantic import LogicalInputRow, LogicalOutput, simulate_stream


def simulate_stream_with_oracles(
    module: CircuitModule,
    input_stream: list[LogicalInputRow],
    oracle_stream: list[Mapping[str, object]],
    *,
    state_timing: StateTimingPlan | None = None,
) -> list[tuple[LogicalOutput, ...]]:
    """Evaluate a Level module against ordinary inputs plus a scripted oracle trace.

    The oracle trace is kept separate from ordinary inputs so reference simulation cannot
    accidentally blur the semantic distinction between externally wired ports and compiler-owned
    physical observations.  Oracle values themselves remain ordinary deterministic samples once a
    trace has been supplied.
    """

    if len(input_stream) != len(oracle_stream):
        raise ValueError("input_stream and oracle_stream must contain the same number of rows")

    declared = oracle_names(module)
    merged: list[LogicalInputRow] = []
    for index, (input_row, oracle_row) in enumerate(zip(input_stream, oracle_stream, strict=True)):
        overlap = declared & input_row.keys()
        if overlap:
            names = ", ".join(repr(name) for name in sorted(overlap))
            raise ValueError(f"row {index} supplies oracle value(s) through input_stream: {names}")
        unknown = set(oracle_row) - declared
        if unknown:
            names = ", ".join(repr(name) for name in sorted(unknown))
            raise ValueError(f"row {index} supplies undeclared oracle value(s): {names}")
        missing = declared - oracle_row.keys()
        if missing:
            names = ", ".join(repr(name) for name in sorted(missing))
            raise ValueError(f"row {index} is missing oracle value(s): {names}")
        merged.append({**input_row, **oracle_row})

    return simulate_stream(module, merged, state_timing=state_timing)
