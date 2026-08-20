"""Electrically isolated realization of temporal scalar delay buses.

The first temporal bus lowerer connected semantic producer nets directly to shared ``Each + 0 ->
Each`` trunks and returned shared trunk nets directly to ordinary consumers. Both directions are
unsafe under Factorio's two-wire physical model: same-color coalescing can contaminate a producer
network backwards, while one shared trunk color can impose incompatible red/green requirements at
unrelated downstream consumers.

This experimental lowerer therefore treats the bus as a real multiplexed transport fabric:

* one signal-specific one-tick ingress copy electrically isolates every lane from its producer;
* ingress copies available on the same ``(bus, phase)`` are explicitly wired onto one aggregate,
  bus-private ingress net;
* shared ``Each`` stages carry only bus-private nets; and
* one signal-specific one-tick egress copy isolates every tap from the shared trunk before the value
  reaches an ordinary semantic consumer.

A one-tick scalar lifetime never enters the shared fabric at all: it is cheaper and safer to retain
the ordinary private ``signal + 0 -> signal`` delay. Ingress and egress copies replace the first and
last ticks of longer private scalar delay chains, so the logical observation phase does not change.
Only the middle transport ticks are shared.
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
    """Temporal plan lowerer with electrical firewalls on both sides of shared scalar buses."""

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
        self._bus_egress_by_lane_phase: dict[tuple[int, int, int], RealizedValue] = {}

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
            self._attach(output_net, output_endpoint)
            builder = self.net_builders[output_net]
            if value.signal not in builder.signals:
                builder.signals = tuple(sorted((*builder.signals, value.signal)))

        result = RealizedValue(
            signal=value.signal,
            net=output_net,
            phase=output_phase,
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

    def _bus_egress(
        self,
        bus: int,
        lane: DelayBusLane,
        trunk: RealizedValue,
        target_phase: int,
    ) -> RealizedValue:
        """Copy one lane off a bus-private trunk before exposing it to semantic consumers."""

        key = (bus, lane.producer, target_phase)
        cached = self._bus_egress_by_lane_phase.get(key)
        if cached is not None:
            return cached
        if trunk.phase + 1 != target_phase:
            raise ValueError("isolated delay-bus egress must consume the immediately preceding tick")
        if not isinstance(trunk.signal, int):
            raise ValueError("scalar delay buses require compiler-allocated abstract signals")

        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(signal=trunk.signal, nets=(trunk.net,)),
            right=Operand(constant=0),
            output_each=False,
            output_signal=trunk.signal,
            description="phase alignment delay",
        )
        self.circuit.entities.append(entity)
        self._attach(trunk.net, Endpoint(entity.id, Connector.INPUT))
        output_net = self._new_net(
            (trunk.signal,),
            Endpoint(entity.id, Connector.OUTPUT),
            label=f"scalar delay bus {bus} isolated egress @ {target_phase}",
        )
        result = RealizedValue(
            signal=trunk.signal,
            net=output_net,
            phase=target_phase,
            clean_single_lane=False,
        )
        window = self._scalar_window(trunk)
        if window is None:
            self._remember_scalar(result, self._point_window(target_phase))
        else:
            self._remember_scalar(
                result,
                self._window_after_exact_alignment(window, trunk.phase, target_phase),
            )
        self._bus_egress_by_lane_phase[key] = result
        return result

    def _private_exact_tick(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        """Transport one scalar tick without consulting settling, observation, or bus policy."""

        if target_phase != value.phase + 1:
            raise ValueError("private exact bus tick must advance by exactly one phase")
        return self.exact_delay_to(value, target_phase)

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

        # A one-tick use has no shareable middle transport. Do not create an aggregate ingress net
        # only to expose it immediately to an ordinary consumer: keep this exact tick private.
        if target_phase == value.phase + 1:
            return self._private_exact_tick(value, target_phase)

        current = value
        if current.phase == lane.start_phase:
            current = self._bus_ingress(bus, lane, current, binding)
        if target_phase <= current.phase:
            raise ValueError("delay-bus target does not leave room for isolated egress")

        # A two-tick lifetime has no shared middle stage: ingress and egress simply replace the two
        # private delays. Longer lifetimes join the shared trunk after ingress and leave it one tick
        # before the consumer so the final egress copy preserves the requested target phase.
        self._register_isolated_bus_join(bus, lane, current)
        trunk_phase = target_phase - 1
        if trunk_phase == current.phase:
            trunk = current
        else:
            for phase in range(current.phase, trunk_phase):
                self._ensure_bus_stage(bus, phase)
            output_net = self._bus_stage_output_net[(bus, trunk_phase - 1)]
            trunk = RealizedValue(
                signal=current.signal,
                net=output_net,
                phase=trunk_phase,
                clean_single_lane=False,
            )
            window = self._scalar_window(current)
            if window is None:
                self._remember_scalar(trunk, self._point_window(trunk_phase))
            else:
                self._remember_scalar(
                    trunk,
                    self._window_after_exact_alignment(window, current.phase, trunk_phase),
                )

        return self._bus_egress(bus, lane, trunk, target_phase)

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
        # Keep one tuple per signal even when several lanes share the same aggregate ingress net.
        # _ensure_bus_stage deduplicates input net ids but uses these tuples to recover the complete
        # lane set that must appear on the stage output.
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
    """Lower one unpacked periodic Level module with electrically isolated scalar buses."""

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
