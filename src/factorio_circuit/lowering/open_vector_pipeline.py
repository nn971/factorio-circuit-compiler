"""Canonical whole-vector lowering pipeline."""

from typing import cast

from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.ir.abstract_physical import AbstractNet, AbstractPhysicalCircuit
from factorio_circuit.ir.semantic import (
    CircuitModule,
    ScalarValue,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    reject_event_module,
    validate_canonical_module,
)
from factorio_circuit.ir.state import AccumulatorRegister, FreezeRegister, VectorRegisterRead
from factorio_circuit.lowering.input_sampling import SamplingPolicyLowerer
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.sampling import SamplingPolicy

_VECTOR_OUTPUTS = (
    VectorInput,
    VectorInputSample,
    VectorConstant,
    VectorRegisterRead,
    VectorBinaryOp,
    VectorScalarOp,
    VectorFilter,
    VectorSelect,
)


def _lowering_cost(circuit: AbstractPhysicalCircuit) -> tuple[int, int, int]:
    """Rank alternative lowerings by physical size, then graph complexity.

    Packing is an implementation choice, not a semantic requirement. A compatible Each/Each or
    Each/constant grouping can still be globally unprofitable once lane isolation, phase alignment,
    and downstream scalar extraction are accounted for. Prefer fewer implementation combinators;
    use net count and total endpoint incidence only as deterministic tie-breakers.
    """

    return (
        circuit.combinator_count,
        len(circuit.nets),
        sum(len(net.endpoints) for net in circuit.nets),
    )


def _lower_once(
    module: CircuitModule,
    *,
    enable_packing: bool,
    state_timing: StateTimingPlan,
    sampling_policy: SamplingPolicy,
) -> AbstractPhysicalCircuit:
    lowerer = SamplingPolicyLowerer(
        module,
        enable_packing=enable_packing,
        state_timing=state_timing,
        sampling_policy=sampling_policy,
    )
    lowerer._create_input_markers()
    if module.state_registers:
        lowerer._reserve_state_outputs()
        lowerer._create_state_components()

    outputs: list[RealizedValue | RealizedVector] = []
    for value in module.output.values:
        if isinstance(value, _VECTOR_OUTPUTS):
            outputs.append(lowerer.realize_vector(value))
        else:
            outputs.append(lowerer.realize(cast(ScalarValue, value)))
    lowerer._create_output_markers(outputs)
    lowerer.circuit.nets = [
        AbstractNet(
            id=net_id,
            signals=builder.signals,
            endpoints=tuple(builder.endpoints),
            label=builder.label,
            fixed_signals=builder.fixed_signals,
            carries_dynamic_vector=builder.carries_dynamic_vector,
        )
        for net_id, builder in sorted(lowerer.net_builders.items())
    ]
    lowerer.circuit.validate()
    return lowerer.circuit


def lower_normalized_vectors(
    module: CircuitModule,
    *,
    enable_packing: bool,
    state_timing: StateTimingPlan,
    sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
) -> AbstractPhysicalCircuit:
    """Lower a module that has already crossed the canonical Level boundary.

    Periodic state cones are scheduled as late as possible toward their transition boundaries.
    Level values also carry lowering-time validity proofs, so stable values can be reused directly;
    when exact transport remains necessary, scalar and vector delay prefixes are shared across
    consumers.

    ``sampling_policy`` controls freshness freedom for live Level observations. Under ``ALAP``, an
    ordinary external input/oracle and supported feed-forward Level logic derived from it may be
    re-observed at a later consumer phase instead of transporting an earlier chosen token through
    identity combinators. Different uses may therefore observe different physical ticks. Explicit
    logical reindexing and explicit exact transport remain token-preserving boundaries.

    Packing is transactional. When requested, lowering produces both packed and unpacked physical
    candidates and keeps the cheaper one. This turns compatibility partitioning into a proposal
    rather than an unconditional commitment: workloads whose packed lanes trigger more downstream
    isolation/extraction work cannot become physically larger merely because packing was enabled.
    """

    reject_event_module(module)
    validate_canonical_module(module)
    unsupported_registers = [
        register
        for register in module.state_registers
        if not isinstance(register, (AccumulatorRegister, FreezeRegister))
    ]
    if unsupported_registers:
        names = ", ".join(register.name for register in unsupported_registers)
        raise ValueError(
            "vector lowering supports AccumulatorReg and FreezeReg state; "
            f"unsupported register(s): {names}"
        )

    unpacked = _lower_once(
        module,
        enable_packing=False,
        state_timing=state_timing,
        sampling_policy=sampling_policy,
    )
    if not enable_packing:
        return unpacked

    packed = _lower_once(
        module,
        enable_packing=True,
        state_timing=state_timing,
        sampling_policy=sampling_policy,
    )
    return min((packed, unpacked), key=_lowering_cost)
