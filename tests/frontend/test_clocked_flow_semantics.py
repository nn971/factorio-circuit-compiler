from dataclasses import FrozenInstanceError, asdict, astuple, fields

import pytest

from factorio_circuit import Circuit
from factorio_circuit.ir.semantic import (
    Clock,
    ClockProvenance,
    Flow,
    InputSample,
    PayloadShape,
    TemporalModality,
    VectorInput,
    VectorInputSample,
)
from factorio_circuit.ir.semantic import Input as IRInput


def test_clocked_vocabulary_is_immutable_and_value_based() -> None:
    clock = Clock(
        identity="finished",
        provenance=ClockProvenance.EXTERNAL_EVENT,
        guaranteed_min_separation=3,
    )
    same_clock = Clock("finished", ClockProvenance.EXTERNAL_EVENT, 3)
    flow = Flow(
        reference="finished",
        payload_shape=PayloadShape.SCALAR,
        modality=TemporalModality.EVENT,
        clock=clock,
        logical_offset=2,
    )

    assert clock == same_clock
    assert clock is not same_clock
    assert clock.guaranteed_min_separation == 3
    assert flow.payload_shape is PayloadShape.SCALAR
    assert flow.modality is TemporalModality.EVENT
    assert flow.clock == clock
    assert flow.logical_offset == 2
    with pytest.raises(FrozenInstanceError):
        clock.identity = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        flow.logical_offset = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "args",
    [
        ("", ClockProvenance.INFERRED, 1),
        ("clock", ClockProvenance.INFERRED, 0),
        ("clock", ClockProvenance.INFERRED, -1),
        ("clock", ClockProvenance.INFERRED, True),
    ],
)
def test_clock_validation_rejects_invalid_identity_or_separation(
    args: tuple[object, object, object],
) -> None:
    with pytest.raises(ValueError):
        Clock(*args)  # type: ignore[arg-type]


def test_legacy_input_dataclasses_remain_acyclic_and_compatible() -> None:
    scalar = IRInput("signal")
    same_scalar = IRInput("signal")
    vector = VectorInput("signals")
    same_vector = VectorInput("signals")

    assert tuple(field.name for field in fields(scalar)) == ("name",)
    assert tuple(field.name for field in fields(vector)) == ("name",)
    assert asdict(scalar) == {"name": "signal"}
    assert astuple(scalar) == ("signal",)
    assert repr(scalar) == "Input(name='signal')"
    assert scalar == same_scalar
    assert hash(scalar) == hash(same_scalar)
    assert asdict(vector) == {"name": "signals"}
    assert astuple(vector) == ("signals",)
    assert repr(vector) == "VectorInput(name='signals')"
    assert vector == same_vector
    assert hash(vector) == hash(same_vector)
    assert not hasattr(scalar, "flow")
    assert not hasattr(vector, "flow")

    scalar_sample = InputSample(scalar, 2)
    vector_sample = VectorInputSample(vector, 2)
    assert asdict(scalar_sample) == {"source": {"name": "signal"}, "offset": 2, "name": None}
    assert asdict(vector_sample) == {"source": {"name": "signals"}, "offset": 2, "name": None}
    assert not hasattr(scalar_sample, "flow")
    assert not hasattr(vector_sample, "flow")


def test_legacy_scalar_and_vector_wrappers_expose_cached_level_flows() -> None:
    circuit = Circuit("level_sources")
    scalar = circuit.input("scalar")
    vector = circuit.signals("vector")

    scalar_flow = scalar.flow
    vector_flow = vector.flow
    assert scalar_flow is scalar.flow
    assert vector_flow is vector.flow
    assert scalar_flow.payload_shape is PayloadShape.SCALAR
    assert scalar_flow.modality is TemporalModality.LEVEL
    assert vector_flow.payload_shape is PayloadShape.VECTOR
    assert vector_flow.modality is TemporalModality.LEVEL
    assert scalar_flow.clock.identity == "level_sources:scalar:scalar"
    assert vector_flow.clock.identity == "level_sources:vector:vector"
    assert asdict(scalar_flow)["reference"] == {"name": "scalar"}
    assert asdict(vector_flow)["reference"] == {"name": "vector"}

    other_circuit = Circuit("other_scope")
    other_scalar = other_circuit.input("scalar")
    assert scalar_flow.clock != other_scalar.flow.clock
    assert scalar_flow != other_scalar.flow


def test_repeated_same_offset_source_sampling_keeps_legacy_cache_identity() -> None:
    circuit = Circuit("sampling_cache")
    scalar = circuit.input("scalar")
    vector = circuit.signals("vector")
    circuit.step(2)

    first_scalar = scalar.sample()
    second_scalar = scalar.sample()
    first_vector = vector.sample()
    second_vector = vector.sample()
    assert first_scalar.ir is second_scalar.ir
    assert first_vector.ir is second_vector.ir
    assert isinstance(first_scalar.ir, InputSample)
    assert isinstance(first_vector.ir, VectorInputSample)
