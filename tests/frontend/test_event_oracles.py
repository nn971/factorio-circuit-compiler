from factorio_circuit import Circuit
from factorio_circuit.ir.oracle import EventOracleInput, oracle_sources
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


def test_scalar_and_vector_event_oracles_use_external_event_flow() -> None:
    circuit = Circuit("event_oracles")
    alarm = circuit.oracle_event("alarm", guaranteed_min_separation=2)
    items = circuit.oracle_signal_event("items", guaranteed_min_separation=1)
    circuit.output("alarm", alarm)
    circuit.output("items", items)

    module = circuit.build()
    assert isinstance(alarm.ir, EventOracleInput)
    assert isinstance(items.ir, EventOracleInput)
    assert alarm.ir.payload_shape is PayloadShape.SCALAR
    assert items.ir.payload_shape is PayloadShape.VECTOR
    assert alarm.flow.modality is TemporalModality.EVENT
    assert items.flow.modality is TemporalModality.EVENT
    assert alarm.ir.clock.guaranteed_min_separation == 2
    assert oracle_sources(module)[-2:] == (alarm.ir, items.ir)


def test_ordinary_event_is_not_an_oracle_source() -> None:
    circuit = Circuit("ordinary_event")
    source = circuit.signal_event("source", guaranteed_min_separation=1)
    circuit.output("source", source)

    assert oracle_sources(circuit.build()) == ()
