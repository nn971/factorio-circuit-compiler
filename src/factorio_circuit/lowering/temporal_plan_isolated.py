"""Electrically isolated realization of temporal scalar delay buses.

The first temporal bus lowerer connected independent semantic producer nets directly to the same
``Each + 0 -> Each`` input connector. Physical synthesis is allowed to coalesce disjoint-signal nets
that meet on one connector, which is normally useful, but here that merge propagates *backwards*
onto producer nets that may still feed clean-single-lane / Each-sensitive consumers elsewhere.

This experimental lowerer inserts one signal-specific one-tick ingress copy per delayed lane before
that lane is allowed onto a shared bus. Ingress outputs are bus-private, so later coalescing is safe:
it can no longer contaminate the original computation net. Ingress copies that become available on
the same ``(bus, phase)`` are wired onto one aggregate bus-private ingress net before the shared bus
stage. That makes the intended physical fan-in explicit and keeps each bus stage within Factorio's
two-wire input limit: at most one ingress net plus the previous bus trunk.

The ingress copy is also the lane's first exact transport tick, so consumers one tick after
production can tap it directly.
"""

from __future__ import annotations

from typing import cast

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.analysis.temporal_hypergraph import TemporalHypergraph
from factorio_circuit.analysis.temporal_optimize import DelayBusLane, TemporalOptimizationResult
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    Endpoint,
    Operand,
)
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
)
from factorio_circuit.ir.state import AccumulatorRegister, FreezeRegister, VectorRegisterRead
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector
from factorio_circuit.lowering.temporal_plan import TemporalPlanLowerer
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

type _BusBinding = tuple[int, DelayBusLane]


class IsolatedTemporalPlanLowerer(TemporalPlanLowerer):
    """Temporal plan lowerer whose shared-bus ingress cannot backfeed producer nets."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        state_timing: StateTimingPlan,
        sampling_policy: SamplingPolicy,
        graph: TemporalHypergraph,
        optimization: TemporalOptimizationResult,
    ) -> None:
        super().__init__(
            module,
            state_timing=state_timing,
            sampling_policy=sampling_policy,
            graph=graph,
            optimization=optimization,
        )
        self._bus_ingress_by_lane: dict[tuple[int, int], RealizedValue] = {}
        self._bus_ingress_net_by_phase: dict[tuple[int, int], int] = {}

    def _bus_ingress(
        self,
        bus: int,
        lane: DelayBusLane,
        value: RealizedValue,
        binding: _BusBinding,
    ) -> RealizedValue:
        """Copy one lane privately, then join the aggregate ingress net for its bus/phase."""

        key = (bus, lane.producer)
        cached = self._bus_ingress_by_lane.get(key)
        if cached is not None:
            return cached
        if value.phase != lane.start_phase:
            raise ValueError("delay-bus ingress must originate at the solved producer phase")
        if not isinstance(value.signal, int):
            raise ValueError("scalar delay buses require compiler-allocated abstract signals")

        latency = FACTORIO_LATENCY.operation_latency("scalar_binary", "delay")
        if latency != 1:  # pragma: no cover - current Factorio target invariant
            raise ValueError("isolated scalar delay-bus ingress requires one-tick arithmetic latency")

        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(signal=value.signal, nets=(value.net,)),
            right=Operand(constant=0),
            output_each=False,
            output_signal=value.signal,
            # Count the electrical firewall as the private first tick of scalar transport.
            description="phase alignment delay",
        )
        self.circuit.entities.append(entity)
        self._attach(value.net, Endpoint(entity.id, Connector.INPUT))

        output_phase = value.phase + latency
        ingress_key = (bus, output_phase)
        output_endpoint = Endpoint(entity.id, Connector.OUTPUT)
        output_net = self._bus_ingress_net_by_phase.get(ingress_key)
        if output_net is None:
            output_net = self._new_net(
                (value.signal,),
                output_endpoint,
                label=f"scalar delay bus {bus} isolated ingress @ {output_phase}",
            )
            self._bus_ingress_net_by_phase[ingress_key] = output_net
        else:
            # All ingress copies on the same bus/phase are already electrically isolated from their
            # semantic producer nets and carry distinct abstract signal identities. Make that safe
            # merge explicit here instead of asking the red/green synthesis heuristic to discover it.
            self._attach(output_net, output_endpoint)
            builder = self.net_builders[output_net]
            if value.signal not in builder.signals:
                builder.signals = tuple(sorted((*builder.signals, value.signal)))

        result = RealizedValue(
            signal=value.signal,
            net=output_net,
            phase=output_phase,
            # The aggregate ingress is intentionally multi-lane when several values join together.
            clean_single_lane=False,
        )
        window = self._scalar_window(value)
        if window is None:
            self._remember_scalar(result, self._point_window(result.phase))
        else:
            self._remember_scalar(
                result,
                self._window_after_exact_alignment(window, value.phase, result.phase),
            )
        self._bus_origin[(result.net, result.signal)] = binding
        self._bus_ingress_by_lane[key] = result
        return result

    def _delay_on_bus(
        self,
        value: RealizedValue,
        target_phase: int,
        binding: _BusBinding,
    ) -> RealizedValue:
        bus, lane = binding
        if target_phase > lane.end_phase:
            raise ValueError(
                f"delay-bus lane {lane.label!r} requested through phase {target_phase}, "
                f"but the optimized lifetime ends at {lane.end_phase}"
            )
        if value.phase < lane.start_phase:
            raise ValueError("delay-bus tap precedes its optimized producer phase")
        if target_phase < value.phase:
            raise ValueError("cannot delay a bus lane backwards in time")
        if target_phase == value.phase:
            return value

        current = value
        if current.phase == lane.start_phase:
            current = self._bus_ingress(bus, lane, current, binding)
        if target_phase == current.phase:
            return current
        if target_phase < current.phase:
            raise ValueError("delay-bus target precedes isolated ingress output")

        self._register_isolated_bus_join(bus, lane, current)
        for phase in range(current.phase, target_phase):
            self._ensure_bus_stage(bus, phase)

        output_net = self._bus_stage_output_net[(bus, target_phase - 1)]
        result = RealizedValue(
            signal=current.signal,
            net=output_net,
            phase=target_phase,
            clean_single_lane=False,
        )
        self._bus_origin[(result.net, result.signal)] = binding

        window = self._scalar_window(current)
        if window is None:
            self._remember_scalar(result, self._point_window(target_phase))
        else:
            self._remember_scalar(
                result,
                self._window_after_exact_alignment(window, current.phase, target_phase),
            )
        return result

    def _register_isolated_bus_join(
        self,
        bus: int,
        lane: DelayBusLane,
        value: RealizedValue,
    ) -> None:
        """Join only a bus-private aggregate ingress net, never the original producer net."""

        key = (bus, lane.producer)
        if key in self._joined_bus_lanes:
            return
        expected = lane.start_phase + 1
        if value.phase != expected:
            raise ValueError(
                f"first isolated delay-bus join for {lane.label!r} occurred at phase "
                f"{value.phase}, expected {expected}"
            )
        self._joined_bus_lanes.add(key)
        self._bus_joins.setdefault((bus, value.phase), []).append((value.net, value.signal))

        stage_key = (bus, value.phase)
        if stage_key in self._bus_stage_entity_index:
            self._add_bus_stage_input(bus, value.phase, value.net)
            self._propagate_bus_signal(bus, value.phase, value.signal)


def lower_normalized_vectors_with_isolated_temporal_plan(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
    sampling_policy: SamplingPolicy,
    graph: TemporalHypergraph,
    optimization: TemporalOptimizationResult,
) -> AbstractPhysicalCircuit:
    """Lower one unpacked periodic Level module with electrically isolated scalar-bus ingress."""

    unsupported_registers = [
        register
        for register in module.state_registers
        if not isinstance(register, (AccumulatorRegister, FreezeRegister))
    ]
    if unsupported_registers:
        names = ", ".join(register.name for register in unsupported_registers)
        raise ValueError(
            "temporal-plan lowering supports AccumulatorReg and FreezeReg state; "
            f"unsupported register(s): {names}"
        )

    lowerer = IsolatedTemporalPlanLowerer(
        module,
        state_timing=state_timing,
        sampling_policy=sampling_policy,
        graph=graph,
        optimization=optimization,
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


__all__ = [
    "IsolatedTemporalPlanLowerer",
    "lower_normalized_vectors_with_isolated_temporal_plan",
]
