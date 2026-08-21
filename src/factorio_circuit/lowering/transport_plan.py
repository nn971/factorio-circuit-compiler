"""Lower observation-aware exact transport plans to abstract physical IR.

The temporal alignment analysis has already classified every placed producer use as same-token
reuse, fresh observation, or residual exact transport.  This lowerer keeps the validated production
ALAP placement fixed, lets the ordinary sampling/settling implementation realize the two free cases,
and intercepts only the residual exact transports chosen by :mod:`analysis.transport_optimize`.

Shared scalar buses are electrically isolated on both sides.  Every bus lane receives a fresh
abstract signal at ingress, shared ``Each + 0 -> Each`` stages carry only bus-private nets,
and every
tap receives another fresh abstract signal at egress.  Concrete Factorio signal identities remain a
later signal-coloring decision, so disconnected abstract lane instances may still reuse one physical
signal while lanes coexisting on the same carrier are forced to differ.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import StateTimingPlan
from factorio_circuit.analysis.temporal_alignment import (
    ExactTransportDemand,
    TemporalAlignmentAnalysis,
    analyze_temporal_alignment,
)
from factorio_circuit.analysis.temporal_hypergraph import (
    TemporalHypergraph,
    TemporalPlacement,
    TemporalPlacementError,
)
from factorio_circuit.analysis.transport_optimize import (
    SharedTransportLane,
    TransportOptimizationResult,
)
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
    Select,
    Value,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorValue,
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

type _BusBinding = tuple[int, SharedTransportLane]


class ObservationAwareTransportLowerer(SamplingPolicyLowerer):
    """Fixed-placement Level lowerer for residual exact transport and isolated scalar buses."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        state_timing: StateTimingPlan,
        sampling_policy: SamplingPolicy,
        graph: TemporalHypergraph,
        placement: TemporalPlacement,
        optimization: TransportOptimizationResult,
    ) -> None:
        graph.validate_placement(placement)
        if graph.period != state_timing.uniform_period:
            raise ValueError("temporal graph period does not match the supplied state timing plan")

        super().__init__(
            module,
            enable_packing=False,
            state_timing=state_timing,
            sampling_policy=sampling_policy,
        )
        self.temporal_graph = graph
        self.temporal_placement = placement
        self.temporal_alignment = analyze_temporal_alignment(graph, placement)
        self.transport_optimization = optimization
        self._validate_transport_plan(self.temporal_alignment, optimization)

        scheduled = dict(self.alap_schedule.output_phases)
        for computation in graph.computations:
            scheduled[id(computation.semantic)] = placement.phase_for(computation.id)
        self.alap_schedule = AlapSchedule(scheduled)

        self._node_by_semantic: dict[int, int] = {
            id(source.semantic): source.id for source in graph.sources
        }
        self._node_by_semantic.update(
            {id(computation.semantic): computation.id for computation in graph.computations}
        )
        self._computation_ids = graph.computation_ids
        self._scalar_producer_by_key: dict[tuple[int, object, int], int] = {}
        self._vector_producer_by_key: dict[tuple[int, int], int] = {}

        self._transport_by_producer = {
            item.producer: item for item in self.temporal_alignment.transports
        }
        self._bus_binding_by_producer: dict[int, _BusBinding] = {}
        for bus in optimization.buses:
            for lane in bus.lanes:
                self._bus_binding_by_producer[lane.producer] = (bus.index, lane)

        self._bus_ingress_by_lane: dict[tuple[int, int], RealizedValue] = {}
        self._bus_ingress_net_by_phase: dict[tuple[int, int], int] = {}
        self._bus_egress_by_lane_phase: dict[tuple[int, int, int], RealizedValue] = {}
        self._joined_bus_lanes: set[tuple[int, int]] = set()
        self._bus_joins: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._bus_stage_entity_index: dict[tuple[int, int], int] = {}
        self._bus_stage_output_net: dict[tuple[int, int], int] = {}

    @staticmethod
    def _transport_key(item: ExactTransportDemand) -> tuple[object, ...]:
        return (
            item.producer,
            item.shape,
            item.start_phase,
            item.end_phase,
            item.tap_phases,
        )

    @classmethod
    def _validate_transport_plan(
        cls,
        alignment: TemporalAlignmentAnalysis,
        optimization: TransportOptimizationResult,
    ) -> None:
        expected = {cls._transport_key(item): item for item in alignment.transports}
        if len(expected) != len(alignment.transports):
            raise TemporalPlacementError("temporal alignment contains duplicate exact transports")

        planned: set[tuple[object, ...]] = set()
        bus_producers: set[int] = set()
        lane_ids: set[int] = set()
        for bus in optimization.buses:
            if not bus.lanes:
                raise TemporalPlacementError("shared transport bus has no lanes")
            expected_start = min(lane.start_phase + 1 for lane in bus.lanes)
            expected_end = max(lane.end_phase - 1 for lane in bus.lanes)
            if (bus.start_phase, bus.end_phase) != (expected_start, expected_end):
                raise TemporalPlacementError(
                    "shared transport bus middle span disagrees with lanes"
                )

            for lane in bus.lanes:
                if lane.lane_id in lane_ids:
                    raise TemporalPlacementError(
                        "shared transport lane ids must be plan-local unique"
                    )
                lane_ids.add(lane.lane_id)
                if lane.producer in bus_producers:
                    raise TemporalPlacementError(
                        "one producer was assigned to multiple shared buses"
                    )
                bus_producers.add(lane.producer)
                matches = [
                    item
                    for item in alignment.transports
                    if item.producer == lane.producer
                    and item.start_phase == lane.start_phase
                    and item.end_phase == lane.end_phase
                    and item.tap_phases == lane.tap_phases
                ]
                if len(matches) != 1:
                    raise TemporalPlacementError(
                        f"shared lane {lane.label!r} does not match one residual exact transport"
                    )
                demand = matches[0]
                if not demand.scalar_bus_candidate:
                    raise TemporalPlacementError("only long scalar exact transports may use a bus")
                planned.add(cls._transport_key(demand))

        for item in optimization.private_transports:
            key = cls._transport_key(item)
            if key not in expected:
                raise TemporalPlacementError("private transport is absent from temporal alignment")
            if item.producer in bus_producers:
                raise TemporalPlacementError("one producer cannot be both private and bus-assigned")
            planned.add(key)

        if planned != set(expected):
            raise TemporalPlacementError("transport plan does not partition all residual demands")

    def _remember_scalar_producer(self, value: RealizedValue, producer: int) -> None:
        key = self._scalar_key(value)
        previous = self._scalar_producer_by_key.setdefault(key, producer)
        if previous != producer:
            raise TemporalPlacementError("one realized scalar was attributed to two temporal nodes")

    def _remember_vector_producer(self, value: RealizedVector, producer: int) -> None:
        key = self._vector_key(value)
        previous = self._vector_producer_by_key.setdefault(key, producer)
        if previous != producer:
            raise TemporalPlacementError("one realized vector was attributed to two temporal nodes")

    def realize(self, value: Value) -> RealizedValue:
        result = super().realize(value)
        producer = self._node_by_semantic.get(id(value))
        if producer is None:
            return result
        if producer in self._computation_ids:
            expected = self.temporal_placement.phase_for(producer)
            if result.phase != expected:
                raise TemporalPlacementError(
                    f"computation {type(value).__name__} realized at phase {result.phase}, "
                    f"but fixed temporal placement requires {expected}"
                )
        self._remember_scalar_producer(result, producer)
        return result

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        result = super().realize_vector(value)
        producer = self._node_by_semantic.get(id(value))
        if producer is None:
            return result
        if producer in self._computation_ids:
            expected = self.temporal_placement.phase_for(producer)
            if result.phase != expected:
                raise TemporalPlacementError(
                    f"vector computation realized at phase {result.phase}, "
                    f"but fixed temporal placement requires {expected}"
                )
        self._remember_vector_producer(result, producer)
        return result

    def _realize_select(self, select: Select) -> RealizedValue:
        """Use the timing-exact arithmetic Select representation for modeled temporal nodes."""

        node = (
            self._node_by_semantic.get(id(select)) if hasattr(self, "_node_by_semantic") else None
        )
        if node is None or node not in self._computation_ids:
            return super()._realize_select(select)

        output_phase = self.temporal_placement.phase_for(node)
        data_phase = output_phase - FACTORIO_LATENCY.operation_latency(
            "select_data", select.name
        )
        condition_phase = output_phase - FACTORIO_LATENCY.operation_latency(
            "select_condition", select.name
        )

        condition = self.realize(select.condition)
        when_true = self._realize_operand_value(select.when_true)
        when_false = self._realize_operand_value(select.when_false)
        condition = self.delay_to(condition, condition_phase)
        if isinstance(when_true, RealizedValue):
            when_true = self.delay_to(when_true, data_phase)
        if isinstance(when_false, RealizedValue):
            when_false = self.delay_to(when_false, data_phase)

        diff = self._emit_binary_from_operands("-", when_true, when_false)
        if diff.phase > condition_phase:
            raise TemporalPlacementError("Select data stage missed its modeled condition boundary")
        diff = self.delay_to(diff, condition_phase)
        gated = self._emit_binary_from_realized("*", diff, condition)

        final_input_phase = output_phase - FACTORIO_LATENCY.operation_latency(
            "scalar_binary", "select-final"
        )
        if gated.phase != final_input_phase:
            raise TemporalPlacementError(
                "Select condition stage disagrees with target latency model"
            )

        final_false = when_false
        if isinstance(final_false, RealizedValue) and final_false.phase < final_input_phase:
            # This is an implementation-internal second use of the already selected data token, not
            # a semantic hypergraph edge.  Preserve it exactly rather than re-observing a live arm.
            final_false = self.exact_delay_to(final_false, final_input_phase)

        result = self._emit_binary_from_operands(
            "+", final_false, gated, description=select.name
        )
        if result.phase != output_phase:
            raise TemporalPlacementError(
                f"Select realized at phase {result.phase}, expected {output_phase}"
            )
        return result

    def _planned_scalar_transport(
        self,
        value: RealizedValue,
        target_phase: int,
    ) -> tuple[int, ExactTransportDemand] | None:
        producer = self._scalar_producer_by_key.get(self._scalar_key(value))
        if producer is None:
            return None
        demand = self._transport_by_producer.get(producer)
        if demand is None or target_phase not in demand.tap_phases:
            return None
        return producer, demand

    def _planned_vector_transport(
        self,
        value: RealizedVector,
        target_phase: int,
    ) -> tuple[int, ExactTransportDemand] | None:
        producer = self._vector_producer_by_key.get(self._vector_key(value))
        if producer is None:
            return None
        demand = self._transport_by_producer.get(producer)
        if demand is None or target_phase not in demand.tap_phases:
            return None
        return producer, demand

    def _free_scalar_at(
        self,
        value: RealizedValue,
        producer: int,
        target_phase: int,
    ) -> RealizedValue:
        before = len(self.circuit.entities)
        result = SamplingPolicyLowerer.delay_to(self, value, target_phase)
        changed_representation = result.net != value.net or result.signal != value.signal
        if len(self.circuit.entities) != before or changed_representation:
            raise TemporalPlacementError(
                "temporal alignment marked a scalar transport start free, but physical lowering "
                "required exact hardware"
            )
        self._remember_scalar_producer(result, producer)
        return result

    def _free_vector_at(
        self,
        value: RealizedVector,
        producer: int,
        target_phase: int,
    ) -> RealizedVector:
        before = len(self.circuit.entities)
        result = SamplingPolicyLowerer.delay_vector_to(self, value, target_phase)
        if len(self.circuit.entities) != before or result.net != value.net:
            raise TemporalPlacementError(
                "temporal alignment marked a vector transport start free, but physical lowering "
                "required exact hardware"
            )
        self._remember_vector_producer(result, producer)
        return result

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        if value.phase == target_phase:
            return value

        planned = self._planned_scalar_transport(value, target_phase)
        if planned is None:
            result = SamplingPolicyLowerer.delay_to(self, value, target_phase)
            producer = self._scalar_producer_by_key.get(self._scalar_key(value))
            if producer is not None and result.net == value.net and result.signal == value.signal:
                self._remember_scalar_producer(result, producer)
            return result

        producer, demand = planned
        start = self._free_scalar_at(value, producer, demand.start_phase)
        binding = self._bus_binding_by_producer.get(producer)
        if binding is None:
            return self.exact_delay_to(start, target_phase)
        _bus, lane = binding
        if target_phase == lane.start_phase + 1:
            # The isolated bus has no profitable shareable middle at this tap. Keep the
            # short branch
            # private even when the same exact token also has later bus taps.
            return self.exact_delay_to(start, target_phase)
        return self._delay_on_bus(start, target_phase, binding)

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        if value.phase == target_phase:
            return value

        planned = self._planned_vector_transport(value, target_phase)
        if planned is None:
            result = SamplingPolicyLowerer.delay_vector_to(self, value, target_phase)
            producer = self._vector_producer_by_key.get(self._vector_key(value))
            if producer is not None and result.net == value.net:
                self._remember_vector_producer(result, producer)
            return result

        producer, demand = planned
        start = self._free_vector_at(value, producer, demand.start_phase)
        return self.exact_delay_vector_to(start, target_phase)

    def _append_bus_signal(self, net: int, signal: int) -> None:
        builder = self.net_builders[net]
        if signal in builder.signals:
            return
        for existing in builder.signals:
            self._add_signal_conflict(
                existing,
                signal,
                "shared transport lanes coexist on one bus carrier",
            )
        builder.signals = tuple(sorted((*builder.signals, signal)))

    def _bus_ingress(
        self,
        bus: int,
        lane: SharedTransportLane,
        value: RealizedValue,
    ) -> RealizedValue:
        key = (bus, lane.producer)
        cached = self._bus_ingress_by_lane.get(key)
        if cached is not None:
            return cached
        if value.phase != lane.start_phase:
            raise TemporalPlacementError(
                "shared-bus ingress must start at residual transport phase"
            )

        bus_signal = self._new_signal(f"transport bus {bus} lane {lane.lane_id}: {lane.label}")
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(signal=value.signal, nets=(value.net,)),
            right=Operand(constant=0),
            output_each=False,
            output_signal=bus_signal,
            description="phase alignment delay: bus ingress",
        )
        self.circuit.entities.append(entity)
        self._attach(value.net, Endpoint(entity.id, Connector.INPUT))

        output_phase = value.phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "delay")
        if output_phase != lane.ingress_phase:
            raise TemporalPlacementError("shared-bus ingress latency disagrees with transport plan")
        output_endpoint = Endpoint(entity.id, Connector.OUTPUT)
        ingress_key = (bus, output_phase)
        output_net = self._bus_ingress_net_by_phase.get(ingress_key)
        if output_net is None:
            output_net = self._new_net(
                (bus_signal,),
                output_endpoint,
                label=f"transport bus {bus} isolated ingress @ {output_phase}",
            )
            self._bus_ingress_net_by_phase[ingress_key] = output_net
        else:
            self._attach(output_net, output_endpoint)
            self._append_bus_signal(output_net, bus_signal)

        result = RealizedValue(
            signal=bus_signal,
            net=output_net,
            phase=output_phase,
            clean_single_lane=False,
        )
        self._remember_scalar(result, self._point_window(output_phase))
        self._bus_ingress_by_lane[key] = result
        return result

    def _bus_egress(
        self,
        bus: int,
        lane: SharedTransportLane,
        trunk: RealizedValue,
        target_phase: int,
    ) -> RealizedValue:
        key = (bus, lane.producer, target_phase)
        cached = self._bus_egress_by_lane_phase.get(key)
        if cached is not None:
            return cached
        if trunk.phase + 1 != target_phase:
            raise TemporalPlacementError(
                "shared-bus egress must consume the immediately prior tick"
            )

        output_signal = self._new_signal(
            f"transport bus {bus} lane {lane.lane_id} egress @ {target_phase}"
        )
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(signal=trunk.signal, nets=(trunk.net,)),
            right=Operand(constant=0),
            output_each=False,
            output_signal=output_signal,
            description="phase alignment delay: bus egress",
        )
        self.circuit.entities.append(entity)
        self._attach(trunk.net, Endpoint(entity.id, Connector.INPUT))
        output_net = self._new_net(
            (output_signal,),
            Endpoint(entity.id, Connector.OUTPUT),
            label=f"transport bus {bus} isolated egress @ {target_phase}",
        )
        result = RealizedValue(
            signal=output_signal,
            net=output_net,
            phase=target_phase,
            clean_single_lane=True,
        )
        self._remember_scalar(result, self._point_window(target_phase))
        self._bus_egress_by_lane_phase[key] = result
        return result

    def _register_bus_join(
        self,
        bus: int,
        lane: SharedTransportLane,
        value: RealizedValue,
    ) -> None:
        key = (bus, lane.producer)
        if key in self._joined_bus_lanes:
            return
        if value.phase != lane.ingress_phase or not isinstance(value.signal, int):
            raise TemporalPlacementError(
                "first bus join must be the isolated abstract ingress lane"
            )
        self._joined_bus_lanes.add(key)
        self._bus_joins.setdefault((bus, value.phase), []).append((value.net, value.signal))

        stage_key = (bus, value.phase)
        if stage_key in self._bus_stage_entity_index:
            self._add_bus_stage_input(bus, value.phase, value.net)
            self._propagate_bus_signal(bus, value.phase, value.signal)

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
            raise TemporalPlacementError(
                f"transport bus {bus} has no lane feeding stage {phase}->{phase + 1}"
            )

        signals: set[int] = set()
        if previous_net is not None:
            signals.update(self.net_builders[previous_net].signals)
        signals.update(signal for _net, signal in joins)

        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="+",
            left=Operand(each=True, nets=tuple(inputs)),
            right=Operand(constant=0),
            output_each=True,
            description=f"shared exact transport bus {bus}",
        )
        self.circuit.entities.append(entity)
        self._bus_stage_entity_index[key] = len(self.circuit.entities) - 1
        endpoint = Endpoint(entity.id, Connector.INPUT)
        for net in inputs:
            self._attach(net, endpoint)

        output_net = self._new_net(
            tuple(sorted(signals)),
            Endpoint(entity.id, Connector.OUTPUT),
            label=f"shared exact transport bus {bus} @ {phase + 1}",
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
            raise AssertionError("transport-bus stage is not an Each arithmetic combinator")
        if net in entity.left.nets:
            return
        self.circuit.entities[index] = replace(
            entity,
            left=Operand(each=True, nets=(*entity.left.nets, net)),
        )
        self._attach(net, Endpoint(entity.id, Connector.INPUT))

    def _propagate_bus_signal(self, bus: int, start_phase: int, signal: int) -> None:
        phase = start_phase
        while (bus, phase) in self._bus_stage_output_net:
            output_net = self._bus_stage_output_net[(bus, phase)]
            self._append_bus_signal(output_net, signal)
            phase += 1

    def _delay_on_bus(
        self,
        value: RealizedValue,
        target_phase: int,
        binding: _BusBinding,
    ) -> RealizedValue:
        bus, lane = binding
        if value.phase != lane.start_phase:
            raise TemporalPlacementError(
                "shared transport must enter from its planned capture phase"
            )
        if target_phase not in lane.tap_phases or target_phase < lane.start_phase + 2:
            raise TemporalPlacementError("requested phase is not a shareable tap of this lane")

        ingress = self._bus_ingress(bus, lane, value)
        self._register_bus_join(bus, lane, ingress)
        trunk_phase = target_phase - 1
        if trunk_phase == ingress.phase:
            trunk = ingress
        else:
            for phase in range(ingress.phase, trunk_phase):
                self._ensure_bus_stage(bus, phase)
            output_net = self._bus_stage_output_net[(bus, trunk_phase - 1)]
            trunk = RealizedValue(
                signal=ingress.signal,
                net=output_net,
                phase=trunk_phase,
                clean_single_lane=False,
            )
            self._remember_scalar(trunk, self._point_window(trunk_phase))
        return self._bus_egress(bus, lane, trunk, target_phase)


def lower_normalized_vectors_with_observation_aware_transport(
    module: CircuitModule,
    *,
    state_timing: StateTimingPlan,
    sampling_policy: SamplingPolicy,
    graph: TemporalHypergraph,
    placement: TemporalPlacement,
    optimization: TransportOptimizationResult,
) -> AbstractPhysicalCircuit:
    """Lower one periodic Level module with residual exact transport and isolated scalar buses."""

    unsupported_registers = [
        register
        for register in module.state_registers
        if not isinstance(register, (AccumulatorRegister, FreezeRegister))
    ]
    if unsupported_registers:
        names = ", ".join(register.name for register in unsupported_registers)
        raise ValueError(
            "observation-aware transport lowering supports AccumulatorReg and FreezeReg state; "
            f"unsupported register(s): {names}"
        )

    lowerer = ObservationAwareTransportLowerer(
        module,
        state_timing=state_timing,
        sampling_policy=sampling_policy,
        graph=graph,
        placement=placement,
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
    "ObservationAwareTransportLowerer",
    "lower_normalized_vectors_with_observation_aware_transport",
]
