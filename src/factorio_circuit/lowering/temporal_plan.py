"""Realize an optimized periodic temporal plan in abstract physical IR.

The global optimizer chooses output phases for state-cone computations and partitions phase-specific
scalar values into delay buses. This lowerer reuses the ordinary settling/sampling implementation
for semantics, but replaces its state-cone ALAP deadlines with the optimized placement and lets
assigned scalar values share ``Each + 0 -> Each`` stages.

This is deliberately an experimental, unpacked path. Keeping arithmetic packing disabled makes one
semantic computation correspond to one physical producer, which is the assumption made by the
first exact optimizer. Packing/fusion choices can become solver alternatives in a later milestone.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from factorio_circuit.analysis.temporal_hypergraph import TemporalHypergraph
from factorio_circuit.analysis.temporal_optimize import (
    DelayBusLane,
    TemporalOptimizationResult,
)
from factorio_circuit.analysis.state_timing import StateTimingPlan
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
    Value,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
)
from factorio_circuit.ir.state import AccumulatorRegister, FreezeRegister, VectorRegisterRead
from factorio_circuit.lowering.alap import AlapSchedule
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


type _BusBinding = tuple[int, DelayBusLane]


class TemporalPlanLowerer(SamplingPolicyLowerer):
    """Sampling-aware Level lowerer driven by a solved temporal placement/bus plan."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        state_timing: StateTimingPlan,
        sampling_policy: SamplingPolicy,
        graph: TemporalHypergraph,
        optimization: TemporalOptimizationResult,
    ) -> None:
        graph.validate_placement(optimization.placement)
        if graph.period != state_timing.uniform_period:
            raise ValueError("temporal graph period does not match the supplied state timing plan")

        super().__init__(
            module,
            enable_packing=False,
            state_timing=state_timing,
            sampling_policy=sampling_policy,
        )
        self.temporal_graph = graph
        self.temporal_optimization = optimization

        # Preserve the ordinary ALAP deadlines for nodes outside the first state-cone optimization
        # scope, while overriding every modeled computation with the globally chosen phase.
        scheduled = dict(self.alap_schedule.output_phases)
        for computation in graph.computations:
            scheduled[id(computation.semantic)] = optimization.placement.phase_for(computation.id)
        self.alap_schedule = AlapSchedule(scheduled)

        self._bus_binding_by_semantic: dict[int, _BusBinding] = {}
        for bus in optimization.buses:
            for lane in bus.lanes:
                computation = graph.computation_by_id(lane.producer)
                expected = optimization.placement.phase_for(computation.id)
                if lane.start_phase != expected:
                    raise ValueError("delay-bus lane start does not match computation placement")
                key = id(computation.semantic)
                if key in self._bus_binding_by_semantic:
                    raise ValueError("one computation was assigned to multiple scalar delay buses")
                self._bus_binding_by_semantic[key] = (bus.index, lane)

        # A realized scalar producer is mapped back to its optimized bus lane. Bus taps retain this
        # origin so a later consumer can extend the same shared pipeline rather than branching into
        # a private identity chain.
        self._bus_origin: dict[tuple[int, object], _BusBinding] = {}
        self._joined_bus_lanes: set[tuple[int, int]] = set()
        self._bus_joins: dict[tuple[int, int], list[tuple[int, object]]] = {}
        self._bus_stage_entity_index: dict[tuple[int, int], int] = {}
        self._bus_stage_output_net: dict[tuple[int, int], int] = {}

    def realize(self, value: Value) -> RealizedValue:
        if isinstance(value, VectorSignal):
            # A lane projection is a zero-latency electrical view. Do not inherit the old ALAP
            # deadline for this view: the newly placed scalar consumer should decide when a live or
            # stable vector lane is observed. This is particularly important for movement inputs,
            # which remain resampleable only while they still denote the raw phase-zero source net.
            cached = self.memo.get(id(value))
            if cached is not None:
                return cached
            vector = self.realize_vector(value.vector)
            result = RealizedValue(
                value.signal,
                vector.net,
                vector.phase,
                clean_single_lane=False,
            )
            self.memo[id(value)] = result
            self._record_scalar_semantics(value, result)
            return result

        result = super().realize(value)
        binding = self._bus_binding_by_semantic.get(id(value))
        if binding is not None:
            _bus, lane = binding
            if result.phase != lane.start_phase:
                raise ValueError(
                    f"optimized producer {lane.label!r} realized at phase {result.phase}, "
                    f"expected {lane.start_phase}"
                )
            self._bus_origin[(result.net, result.signal)] = binding
        return result

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        if value.phase == target_phase:
            return value

        # Let the established correctness-preserving free cases win first: a held settling value
        # needs no transport, and an ALAP external Level source can simply be observed at the later
        # phase. The solver deliberately excludes held-only computations from bus cost for this
        # reason.
        window = self._scalar_window(value)
        if (
            not self._force_exact_alignment
            and window is not None
            and window.contains(target_phase)
        ) or self._can_resample_scalar(value):
            return super().delay_to(value, target_phase)

        binding = self._bus_origin.get((value.net, value.signal))
        if binding is None:
            return super().delay_to(value, target_phase)
        return self._delay_on_bus(value, target_phase, binding)

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

        self._register_bus_join(bus, lane, value)
        for phase in range(value.phase, target_phase):
            self._ensure_bus_stage(bus, phase)

        output_net = self._bus_stage_output_net[(bus, target_phase - 1)]
        result = RealizedValue(
            signal=value.signal,
            net=output_net,
            phase=target_phase,
            clean_single_lane=False,
        )
        self._bus_origin[(result.net, result.signal)] = binding

        window = self._scalar_window(value)
        if window is None:
            self._remember_scalar(result, self._point_window(target_phase))
        else:
            self._remember_scalar(
                result,
                self._window_after_exact_alignment(window, value.phase, target_phase),
            )
        return result

    def _register_bus_join(
        self,
        bus: int,
        lane: DelayBusLane,
        value: RealizedValue,
    ) -> None:
        key = (bus, lane.producer)
        if key in self._joined_bus_lanes:
            return
        if value.phase != lane.start_phase:
            # The first request for a lane should originate at its producer. A later bus tap already
            # has the same ``(bus, producer)`` key registered and returns above.
            raise ValueError("first delay-bus use did not originate at the producer phase")
        self._joined_bus_lanes.add(key)
        self._bus_joins.setdefault((bus, lane.start_phase), []).append(
            (value.net, value.signal)
        )

        stage_key = (bus, lane.start_phase)
        if stage_key in self._bus_stage_entity_index:
            self._add_bus_stage_input(bus, lane.start_phase, value.net)
            self._propagate_bus_signal(bus, lane.start_phase, value.signal)

    def _ensure_bus_stage(self, bus: int, phase: int) -> None:
        key = (bus, phase)
        if key in self._bus_stage_entity_index:
            return

        inputs: list[int] = []
        previous_net = self._bus_stage_output_net.get((bus, phase - 1))
        if previous_net is not None:
            inputs.append(previous_net)
        joins = self._bus_joins.get(key, ())
        inputs.extend(net for net, _signal in joins)
        inputs = list(dict.fromkeys(inputs))
        if not inputs:
            raise ValueError(
                f"scalar delay bus {bus} has no source feeding stage {phase}->{phase + 1}"
            )

        signals: set[int] = set()
        if previous_net is not None:
            signals.update(self.net_builders[previous_net].signals)
        for _net, signal in joins:
            if not isinstance(signal, int):
                raise ValueError("scalar delay buses require compiler-allocated abstract signals")
            signals.add(signal)

        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(each=True, nets=tuple(inputs)),
            right=Operand(constant=0),
            output_each=True,
            description=f"scalar phase delay bus {bus}",
        )
        self.circuit.entities.append(entity)
        self._bus_stage_entity_index[key] = len(self.circuit.entities) - 1
        endpoint = Endpoint(entity.id, Connector.INPUT)
        for net in inputs:
            self._attach(net, endpoint)

        output_net = self._new_net(
            tuple(sorted(signals)),
            Endpoint(entity.id, Connector.OUTPUT),
            label=f"scalar phase delay bus {bus} @ {phase + 1}",
        )
        self._bus_stage_output_net[key] = output_net

        next_key = (bus, phase + 1)
        if next_key in self._bus_stage_entity_index:
            self._add_bus_stage_input(bus, phase + 1, output_net)
            for signal in signals:
                self._propagate_bus_signal(bus, phase + 1, signal)

    def _add_bus_stage_input(self, bus: int, phase: int, net: int) -> None:
        key = (bus, phase)
        index = self._bus_stage_entity_index[key]
        entity = self.circuit.entities[index]
        if not isinstance(entity, ArithmeticCombinator) or not entity.left.each:
            raise AssertionError("delay-bus stage is not an Each arithmetic combinator")
        if net in entity.left.nets:
            return
        left = Operand(each=True, nets=(*entity.left.nets, net))
        self.circuit.entities[index] = replace(entity, left=left)
        self._attach(net, Endpoint(entity.id, Connector.INPUT))

    def _propagate_bus_signal(self, bus: int, start_phase: int, signal: object) -> None:
        if not isinstance(signal, int):
            raise ValueError("scalar delay buses require compiler-allocated abstract signals")
        phase = start_phase
        while (bus, phase) in self._bus_stage_output_net:
            output_net = self._bus_stage_output_net[(bus, phase)]
            builder = self.net_builders[output_net]
            if signal not in builder.signals:
                builder.signals = tuple(sorted((*builder.signals, signal)))
            phase += 1


def lower_normalized_vectors_with_temporal_plan(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
    sampling_policy: SamplingPolicy,
    graph: TemporalHypergraph,
    optimization: TemporalOptimizationResult,
) -> AbstractPhysicalCircuit:
    """Lower one unpacked periodic Level module using a solved temporal plan."""

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

    lowerer = TemporalPlanLowerer(
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


__all__ = ["TemporalPlanLowerer", "lower_normalized_vectors_with_temporal_plan"]
