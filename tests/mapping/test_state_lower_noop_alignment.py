from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.mapping.state_lower_entry import _BoundarySafeMappedPeriodicStateLowerer


def test_scalar_same_phase_alignment_does_not_consume_planned_delivery() -> None:
    lowerer = object.__new__(_BoundarySafeMappedPeriodicStateLowerer)
    sentinel = object()
    lowerer.delivery_queues = {(7, 11): [sentinel]}
    lowerer.scalar_origin = {(3, 5): 7}
    value = RealizedValue(signal=5, net=3, phase=11)

    result = lowerer.delay_to(value, 11)

    assert result is value
    assert lowerer.delivery_queues[(7, 11)] == [sentinel]


def test_vector_same_phase_alignment_does_not_consume_planned_delivery() -> None:
    lowerer = object.__new__(_BoundarySafeMappedPeriodicStateLowerer)
    sentinel = object()
    lowerer.delivery_queues = {(9, 13): [sentinel]}
    lowerer.vector_origin = {4: 9}
    value = RealizedVector(net=4, phase=13)

    result = lowerer.delay_vector_to(value, 13)

    assert result is value
    assert lowerer.delivery_queues[(9, 13)] == [sentinel]
