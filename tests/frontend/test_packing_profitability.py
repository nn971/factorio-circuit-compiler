from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from factorio_circuit import Circuit
from factorio_circuit.analysis.state_timing import analyze_normalized_state_timing
from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit
from factorio_circuit.lowering import open_vector_pipeline
from factorio_circuit.lowering.frontend_to_ir import normalize_module


def _normalized_identity_module():
    circuit = Circuit("packing_profitability")
    value = circuit.input("value")
    circuit.output("value", value)
    return normalize_module(circuit.build())


def _candidate(combinators: int, nets: int) -> AbstractPhysicalCircuit:
    fake_nets = tuple(SimpleNamespace(endpoints=()) for _ in range(nets))
    return cast(
        AbstractPhysicalCircuit,
        SimpleNamespace(combinator_count=combinators, nets=fake_nets),
    )


def test_transactional_packing_rejects_globally_larger_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _normalized_identity_module()
    timing = analyze_normalized_state_timing(module)
    unpacked = _candidate(10, 12)
    packed = _candidate(11, 4)

    def fake_lower_once(*_args, enable_packing: bool, **_kwargs):
        return packed if enable_packing else unpacked

    monkeypatch.setattr(open_vector_pipeline, "_lower_once", fake_lower_once)

    result = open_vector_pipeline.lower_normalized_vectors(
        module,
        enable_packing=True,
        state_timing=timing,
    )
    assert result is unpacked


def test_transactional_packing_keeps_smaller_packed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _normalized_identity_module()
    timing = analyze_normalized_state_timing(module)
    unpacked = _candidate(10, 12)
    packed = _candidate(9, 20)

    def fake_lower_once(*_args, enable_packing: bool, **_kwargs):
        return packed if enable_packing else unpacked

    monkeypatch.setattr(open_vector_pipeline, "_lower_once", fake_lower_once)

    result = open_vector_pipeline.lower_normalized_vectors(
        module,
        enable_packing=True,
        state_timing=timing,
    )
    assert result is packed


def test_transactional_packing_uses_net_count_as_tie_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _normalized_identity_module()
    timing = analyze_normalized_state_timing(module)
    unpacked = _candidate(10, 12)
    packed = _candidate(10, 8)

    def fake_lower_once(*_args, enable_packing: bool, **_kwargs):
        return packed if enable_packing else unpacked

    monkeypatch.setattr(open_vector_pipeline, "_lower_once", fake_lower_once)

    result = open_vector_pipeline.lower_normalized_vectors(
        module,
        enable_packing=True,
        state_timing=timing,
    )
    assert result is packed
