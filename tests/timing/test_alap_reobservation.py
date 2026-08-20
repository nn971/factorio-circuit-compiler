from factorio_circuit import Circuit, SamplingPolicy, lower_to_abstract_physical
from factorio_circuit.ir.semantic import Compare
from factorio_circuit.lowering.input_sampling import SamplingPolicyLowerer


def _comparison_module():
    circuit = Circuit("alap_reobservation")
    source = circuit.input("source")
    predicate = source != 0
    circuit.output("predicate", predicate)
    baseline = lower_to_abstract_physical(
        circuit,
        optimize=False,
        sampling_policy=SamplingPolicy.ALAP,
    )
    comparison = next(
        operation
        for operation in baseline.optimized_ir.operations
        if isinstance(operation, Compare)
    )
    return baseline, comparison


def test_alap_reobserves_live_derived_scalar_without_transport() -> None:
    baseline, comparison = _comparison_module()
    lowerer = SamplingPolicyLowerer(
        baseline.optimized_ir,
        enable_packing=False,
        state_timing=baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
    )
    lowerer._create_input_markers()

    realized = lowerer.realize(comparison)
    before = len(lowerer.circuit.entities)
    later = lowerer.delay_to(realized, realized.phase + 3)

    assert later.net == realized.net
    assert later.signal == realized.signal
    assert later.phase == realized.phase + 3
    assert len(lowerer.circuit.entities) == before


def test_exact_transport_freezes_live_derived_scalar() -> None:
    baseline, comparison = _comparison_module()
    lowerer = SamplingPolicyLowerer(
        baseline.optimized_ir,
        enable_packing=False,
        state_timing=baseline.state_timing,
        sampling_policy=SamplingPolicy.ALAP,
    )
    lowerer._create_input_markers()

    realized = lowerer.realize(comparison)
    before = len(lowerer.circuit.entities)
    later = lowerer.exact_delay_to(realized, realized.phase + 3)

    assert later.net != realized.net
    assert later.phase == realized.phase + 3
    assert len(lowerer.circuit.entities) - before == 3
