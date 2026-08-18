from factorio_circuit.analysis.state_timing import ClockDomainTiming, StateTimingPlan
from factorio_circuit.experimental.delay_bypass_lowering import DelayBypassVectorLowerer
from factorio_circuit.ir.semantic import CircuitModule, ReturnValue
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector


def _lowerer() -> DelayBypassVectorLowerer:
    module = CircuitModule("delay_bypass", (), (), ReturnValue(()))
    timing = StateTimingPlan((ClockDomainTiming(0, 60, ()),), ())
    return DelayBypassVectorLowerer(module, enable_packing=False, state_timing=timing)


def test_scalar_within_period_alignment_reuses_same_physical_value() -> None:
    lowerer = _lowerer()
    source = RealizedValue(signal=7, net=11, phase=2)

    result = lowerer.delay_to(source, 50)

    assert result.signal == source.signal
    assert result.net == source.net
    assert result.phase == 50
    assert lowerer.circuit.entities == []
    stats = lowerer.stats()
    assert stats.scalar_alignment_calls_bypassed == 1
    assert stats.scalar_alignment_ticks_bypassed == 48


def test_vector_within_period_alignment_reuses_same_physical_net() -> None:
    lowerer = _lowerer()
    source = RealizedVector(net=13, phase=4)

    result = lowerer.delay_vector_to(source, 57)

    assert result.net == source.net
    assert result.phase == 57
    assert lowerer.circuit.entities == []
    stats = lowerer.stats()
    assert stats.vector_alignment_calls_bypassed == 1
    assert stats.vector_alignment_ticks_bypassed == 53
