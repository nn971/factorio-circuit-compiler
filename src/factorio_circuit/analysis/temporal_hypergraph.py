"""Temporal computation hypergraph for global physical phase placement.

ASAP/ALAP are useful extremal schedules, but neither minimizes physical transport in general. This
module exposes the periodic Level state cone as a target-timed hypergraph before phase-delay
combinators are emitted. Computation nodes have legal mobility windows, state-update boundaries are
fixed sinks, and one produced value may feed many consumers through one hyperedge/lifetime.

The first milestone deliberately treats ``VectorSignal`` as a zero-cost view of its underlying whole
vector. Moving a lane read therefore moves the observation of that vector rather than inventing a
scalar computation node. Whole-vector transport remains ordinary cost until a vector-bus design is
added.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    FlowInput,
    FlowInputSample,
    FlowVectorInput,
    FlowVectorInputSample,
    Input,
    InputSample,
    PayloadShape,
    Select,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    is_vector_value,
    reject_event_module,
    validate_canonical_module,
)
from factorio_circuit.ir.state import VectorRegisterRead, state_transitions
from factorio_circuit.sampling import SamplingPolicy

from .latency import FACTORIO_LATENCY
from .state_timing import StateTimingPlan


class TemporalPlacementError(ValueError):
    """Raised when a temporal hypergraph or proposed placement is infeasible."""


class TemporalSourceMode(StrEnum):
    """How a leaf can be observed inside one periodic physical reaction."""

    LIVE = "live"
    STABLE = "stable"
    EXACT = "exact"


@dataclass(frozen=True, slots=True)
class TemporalSource:
    id: int
    label: str
    shape: PayloadShape
    mode: TemporalSourceMode
    start_phase: int
    end_phase_exclusive: int | None
    semantic: object


@dataclass(frozen=True, slots=True)
class TemporalComputation:
    id: int
    label: str
    shape: PayloadShape
    earliest_phase: int
    latest_phase: int
    semantic: object

    @property
    def mobility(self) -> int:
        return self.latest_phase - self.earliest_phase

    @property
    def delay_bus_eligible(self) -> bool:
        return self.shape is PayloadShape.SCALAR and isinstance(
            self.semantic,
            (BinaryOp, Compare, Select),
        )


@dataclass(frozen=True, slots=True)
class TemporalSink:
    id: int
    label: str
    shape: PayloadShape
    phase: int


@dataclass(frozen=True, slots=True)
class TemporalArc:
    """One producer use.

    For a computation consumer, ``latency`` is that consumer's physical input-to-output latency.
    The producer must therefore be available by ``phase(consumer) - latency``. A fixed sink consumes
    directly at its ``phase`` and uses latency zero.
    """

    producer: int
    consumer: int
    latency: int
    shape: PayloadShape


@dataclass(frozen=True, slots=True)
class TemporalPlacement:
    phases: tuple[tuple[int, int], ...]

    def phase_for(self, computation: TemporalComputation | int) -> int:
        node_id = computation if isinstance(computation, int) else computation.id
        for candidate, phase in self.phases:
            if candidate == node_id:
                return phase
        raise KeyError(node_id)


@dataclass(frozen=True, slots=True)
class TransportInterval:
    producer: int
    label: str
    shape: PayloadShape
    start_phase: int
    end_phase: int
    delay_bus_eligible: bool

    @property
    def length(self) -> int:
        return self.end_phase - self.start_phase


@dataclass(frozen=True, slots=True)
class TemporalTransportCost:
    scalar_serial: int
    vector_serial: int
    bus_eligible_scalar_serial: int
    intervals: tuple[TransportInterval, ...]

    @property
    def total_serial(self) -> int:
        return self.scalar_serial + self.vector_serial


@dataclass(frozen=True, slots=True)
class TemporalHypergraph:
    """Periodic state-cone hypergraph with legal phase windows."""

    period: int
    sources: tuple[TemporalSource, ...]
    computations: tuple[TemporalComputation, ...]
    sinks: tuple[TemporalSink, ...]
    arcs: tuple[TemporalArc, ...]

    def source_by_id(self, node_id: int) -> TemporalSource:
        for source in self.sources:
            if source.id == node_id:
                return source
        raise KeyError(node_id)

    def computation_by_id(self, node_id: int) -> TemporalComputation:
        for computation in self.computations:
            if computation.id == node_id:
                return computation
        raise KeyError(node_id)

    def sink_by_id(self, node_id: int) -> TemporalSink:
        for sink in self.sinks:
            if sink.id == node_id:
                return sink
        raise KeyError(node_id)

    @property
    def computation_ids(self) -> frozenset[int]:
        return frozenset(item.id for item in self.computations)

    def asap_placement(self) -> TemporalPlacement:
        placement = TemporalPlacement(
            tuple((item.id, item.earliest_phase) for item in self.computations)
        )
        self.validate_placement(placement)
        return placement

    def alap_placement(self) -> TemporalPlacement:
        placement = TemporalPlacement(
            tuple((item.id, item.latest_phase) for item in self.computations)
        )
        self.validate_placement(placement)
        return placement

    def validate_placement(self, placement: TemporalPlacement) -> None:
        phases = dict(placement.phases)
        expected = set(self.computation_ids)
        if set(phases) != expected:
            raise TemporalPlacementError(
                "placement must assign exactly one phase to every computation"
            )
        if len(phases) != len(placement.phases):
            raise TemporalPlacementError("placement contains duplicate computation assignments")

        computations = {item.id: item for item in self.computations}
        sources = {item.id: item for item in self.sources}
        sinks = {item.id: item for item in self.sinks}
        for node_id, phase in phases.items():
            item = computations[node_id]
            if not item.earliest_phase <= phase <= item.latest_phase:
                raise TemporalPlacementError(
                    f"computation {item.label!r} phase {phase} lies outside "
                    f"[{item.earliest_phase}, {item.latest_phase}]"
                )

        for arc in self.arcs:
            if arc.consumer in computations:
                consumer_input = phases[arc.consumer] - arc.latency
            else:
                consumer_input = sinks[arc.consumer].phase
            if arc.producer in computations:
                if phases[arc.producer] > consumer_input:
                    raise TemporalPlacementError(
                        "temporal dependency requires a producer after its consumer input"
                    )
                continue

            source = sources[arc.producer]
            if consumer_input < source.start_phase:
                raise TemporalPlacementError(
                    f"source {source.label!r} is not available by phase {consumer_input}"
                )

    def transport_intervals(
        self,
        placement: TemporalPlacement,
    ) -> tuple[TransportInterval, ...]:
        """Return structural producer lifetimes for one placement.

        This is a deliberately simple serial upper-bound diagnostic: every computation lifetime is
        counted even when settling persistence could later make it free. The exact optimizer applies
        that persistence classification before forming its objective.
        """

        self.validate_placement(placement)
        phases = dict(placement.phases)
        computations = {item.id: item for item in self.computations}
        sinks = {item.id: item for item in self.sinks}
        outgoing: dict[int, list[TemporalArc]] = {}
        for arc in self.arcs:
            outgoing.setdefault(arc.producer, []).append(arc)

        def consumer_phase(arc: TemporalArc) -> int:
            if arc.consumer in computations:
                return phases[arc.consumer] - arc.latency
            return sinks[arc.consumer].phase

        intervals: list[TransportInterval] = []
        for computation in self.computations:
            uses = outgoing.get(computation.id, ())
            if not uses:
                continue
            start = phases[computation.id]
            end = max(consumer_phase(arc) for arc in uses)
            if end > start:
                intervals.append(
                    TransportInterval(
                        producer=computation.id,
                        label=computation.label,
                        shape=computation.shape,
                        start_phase=start,
                        end_phase=end,
                        delay_bus_eligible=computation.delay_bus_eligible,
                    )
                )

        for source in self.sources:
            if source.mode is not TemporalSourceMode.EXACT:
                continue
            uses = outgoing.get(source.id, ())
            if not uses:
                continue
            end = max(consumer_phase(arc) for arc in uses)
            if end > source.start_phase:
                intervals.append(
                    TransportInterval(
                        producer=source.id,
                        label=source.label,
                        shape=source.shape,
                        start_phase=source.start_phase,
                        end_phase=end,
                        delay_bus_eligible=False,
                    )
                )

        return tuple(
            sorted(
                intervals,
                key=lambda item: (
                    item.start_phase,
                    item.end_phase,
                    item.shape.value,
                    item.producer,
                ),
            )
        )

    def transport_cost(self, placement: TemporalPlacement) -> TemporalTransportCost:
        """Return the structural serial-lifetime upper bound for a placement."""

        intervals = self.transport_intervals(placement)
        scalar = sum(item.length for item in intervals if item.shape is PayloadShape.SCALAR)
        vector = sum(item.length for item in intervals if item.shape is PayloadShape.VECTOR)
        bus_eligible = sum(item.length for item in intervals if item.delay_bus_eligible)
        return TemporalTransportCost(scalar, vector, bus_eligible, intervals)


@dataclass(frozen=True, slots=True)
class _ComputationSeed:
    id: int
    label: str
    shape: PayloadShape
    semantic: object


class _TemporalHypergraphBuilder:
    def __init__(
        self,
        module: CircuitModule,
        timing: StateTimingPlan,
        sampling_policy: SamplingPolicy,
    ) -> None:
        reject_event_module(module)
        validate_canonical_module(module)
        period = timing.uniform_period
        if period is None:
            raise TemporalPlacementError(
                "temporal hypergraph optimization currently requires one uniform periodic domain"
            )
        self.module = module
        self.timing = timing
        self.sampling_policy = sampling_policy
        self.period = period
        self.next_id = 1
        self.sources_by_semantic: dict[int, TemporalSource] = {}
        self.computations_by_semantic: dict[int, _ComputationSeed] = {}
        self.computation_topology: list[int] = []
        self.sinks: list[TemporalSink] = []
        self.arcs: list[TemporalArc] = []
        self.arc_keys: set[tuple[int, int, int, PayloadShape]] = set()

    def _take_id(self) -> int:
        result = self.next_id
        self.next_id += 1
        return result

    @staticmethod
    def _shape(value: object) -> PayloadShape:
        return PayloadShape.VECTOR if is_vector_value(value) else PayloadShape.SCALAR

    @staticmethod
    def _label(value: object) -> str:
        name = getattr(value, "name", None)
        if isinstance(name, str) and name:
            return name
        if isinstance(value, BinaryOp):
            return f"binary {value.op}"
        if isinstance(value, Compare):
            return f"compare {value.op}"
        if isinstance(value, Select):
            return "select"
        if isinstance(value, VectorBinaryOp):
            return f"vector {value.op}"
        if isinstance(value, VectorScalarOp):
            return f"vector-scalar {value.op}"
        if isinstance(value, VectorSelect):
            return f"vector-select {value.op}"
        if isinstance(value, VectorFilter):
            return f"vector-filter {value.op}"
        if isinstance(value, VectorRegisterRead):
            return f"state {value.register.name}"
        return type(value).__name__

    def _source(self, value: object) -> TemporalSource:
        cached = self.sources_by_semantic.get(id(value))
        if cached is not None:
            return cached

        shape = self._shape(value)
        mode: TemporalSourceMode
        start = 0
        end: int | None = None

        if isinstance(value, (Constant, VectorConstant)):
            mode = TemporalSourceMode.STABLE
        elif isinstance(value, VectorRegisterRead):
            read_timing = self.timing.for_read(value)
            register_timing = self.timing.for_register(value.register)
            mode = TemporalSourceMode.STABLE
            start = read_timing.physical_phase
            end = start + register_timing.period
        elif isinstance(
            value,
            (FlowInputSample, InputSample, FlowVectorInputSample, VectorInputSample),
        ):
            start = value.offset * self.period
            mode = (
                TemporalSourceMode.LIVE
                if value.offset == 0 and self.sampling_policy is SamplingPolicy.ALAP
                else TemporalSourceMode.EXACT
            )
            end = self.period if mode is TemporalSourceMode.LIVE else start + 1
        elif isinstance(value, (FlowInput, Input, FlowVectorInput, VectorInput)):
            mode = (
                TemporalSourceMode.LIVE
                if self.sampling_policy is SamplingPolicy.ALAP
                else TemporalSourceMode.EXACT
            )
            end = self.period if mode is TemporalSourceMode.LIVE else 1
        else:  # pragma: no cover - guarded by _visit
            raise TypeError(value)

        source = TemporalSource(
            id=self._take_id(),
            label=self._label(value),
            shape=shape,
            mode=mode,
            start_phase=start,
            end_phase_exclusive=end,
            semantic=value,
        )
        self.sources_by_semantic[id(value)] = source
        return source

    @staticmethod
    def _is_computation(value: object) -> bool:
        return isinstance(
            value,
            (
                BinaryOp,
                Compare,
                Select,
                VectorBinaryOp,
                VectorScalarOp,
                VectorFilter,
                VectorSelect,
            ),
        )

    @staticmethod
    def _children(value: object) -> tuple[tuple[object, int], ...]:
        if isinstance(value, BinaryOp):
            latency = FACTORIO_LATENCY.operation_latency("scalar_binary", value.op)
            return ((value.left, latency), (value.right, latency))
        if isinstance(value, Compare):
            latency = FACTORIO_LATENCY.operation_latency("compare", value.op)
            return ((value.left, latency), (value.right, latency))
        if isinstance(value, Select):
            latency = FACTORIO_LATENCY.operation_latency("select_data", value.name)
            return tuple(
                (item, latency)
                for item in (value.condition, value.when_true, value.when_false)
            )
        if isinstance(value, VectorBinaryOp):
            latency = FACTORIO_LATENCY.operation_latency("vector_binary", value.op)
            return ((value.left, latency), (value.right, latency))
        if isinstance(value, VectorScalarOp):
            latency = FACTORIO_LATENCY.operation_latency("vector_scalar", value.op)
            return ((value.vector, latency), (value.scalar, latency))
        if isinstance(value, (VectorFilter, VectorSelect)):
            family = "vector_select" if isinstance(value, VectorSelect) else "vector_filter"
            latency = FACTORIO_LATENCY.operation_latency(family, value.op)
            return ((value.vector, latency),)
        raise TypeError(value)

    def _add_arc(
        self,
        producer: int,
        consumer: int,
        latency: int,
        shape: PayloadShape,
    ) -> None:
        key = (producer, consumer, latency, shape)
        if key in self.arc_keys:
            return
        self.arc_keys.add(key)
        self.arcs.append(TemporalArc(producer, consumer, latency, shape))

    def _visit(self, value: object) -> int:
        if isinstance(value, VectorSignal):
            return self._visit(value.vector)

        if not self._is_computation(value):
            if isinstance(
                value,
                (
                    Constant,
                    VectorConstant,
                    FlowInput,
                    FlowInputSample,
                    Input,
                    InputSample,
                    FlowVectorInput,
                    FlowVectorInputSample,
                    VectorInput,
                    VectorInputSample,
                    VectorRegisterRead,
                ),
            ):
                return self._source(value).id
            raise TypeError(f"unsupported temporal value {type(value).__name__}")

        cached = self.computations_by_semantic.get(id(value))
        if cached is not None:
            return cached.id

        seed = _ComputationSeed(
            id=self._take_id(),
            label=self._label(value),
            shape=self._shape(value),
            semantic=value,
        )
        self.computations_by_semantic[id(value)] = seed
        for child, latency in self._children(value):
            normalized = child.vector if isinstance(child, VectorSignal) else child
            producer = self._visit(normalized)
            self._add_arc(producer, seed.id, latency, self._shape(normalized))
        self.computation_topology.append(seed.id)
        return seed.id

    def _add_sink(
        self,
        value: object,
        *,
        label: str,
        phase: int,
        shape: PayloadShape,
    ) -> None:
        if phase < 0:
            raise TemporalPlacementError(f"sink {label!r} has negative physical phase {phase}")
        sink = TemporalSink(self._take_id(), label, shape, phase)
        self.sinks.append(sink)
        normalized = value.vector if isinstance(value, VectorSignal) else value
        producer = self._visit(normalized)
        self._add_arc(producer, sink.id, 0, self._shape(normalized))

    def build(self) -> TemporalHypergraph:
        commit_latency = FACTORIO_LATENCY.state_transition_latency("commit")
        for transition in state_transitions(self.module):
            if transition.trigger is not None:
                continue
            timing = self.timing.for_register(transition.register)
            target = timing.transition_input_phase
            if transition.value is not None:
                self._add_sink(
                    transition.value,
                    label=(
                        f"state:{transition.register.name}:{transition.kind}:"
                        f"value:{transition.order}"
                    ),
                    phase=target,
                    shape=PayloadShape.VECTOR,
                )
            if transition.when is not None:
                self._add_sink(
                    transition.when,
                    label=(
                        f"state:{transition.register.name}:{transition.kind}:"
                        f"control:{transition.order}"
                    ),
                    phase=target - commit_latency,
                    shape=PayloadShape.SCALAR,
                )

        seeds = {item.id: item for item in self.computations_by_semantic.values()}
        sources = {item.id: item for item in self.sources_by_semantic.values()}
        sinks = {item.id: item for item in self.sinks}
        incoming: dict[int, list[TemporalArc]] = {}
        for arc in self.arcs:
            incoming.setdefault(arc.consumer, []).append(arc)

        earliest: dict[int, int] = {}
        for node_id in self.computation_topology:
            lower = 0
            for arc in incoming.get(node_id, ()):
                ready = (
                    earliest[arc.producer]
                    if arc.producer in seeds
                    else sources[arc.producer].start_phase
                )
                lower = max(lower, ready + arc.latency)
            earliest[node_id] = lower

        horizon = max((sink.phase for sink in self.sinks), default=max(self.period - 1, 0))
        latest = {node_id: horizon for node_id in seeds}
        for arc in self.arcs:
            if arc.producer in seeds and arc.consumer in sinks:
                latest[arc.producer] = min(
                    latest[arc.producer],
                    sinks[arc.consumer].phase,
                )

        for consumer_id in reversed(self.computation_topology):
            consumer_latest = latest[consumer_id]
            for arc in incoming.get(consumer_id, ()):
                if arc.producer in seeds:
                    latest[arc.producer] = min(
                        latest[arc.producer],
                        consumer_latest - arc.latency,
                    )

        computations: list[TemporalComputation] = []
        for node_id in self.computation_topology:
            seed = seeds[node_id]
            low = earliest[node_id]
            high = latest[node_id]
            if high < low:
                raise TemporalPlacementError(
                    f"computation {seed.label!r} has empty mobility window [{low}, {high}]"
                )
            computations.append(
                TemporalComputation(
                    id=node_id,
                    label=seed.label,
                    shape=seed.shape,
                    earliest_phase=low,
                    latest_phase=high,
                    semantic=seed.semantic,
                )
            )

        graph = TemporalHypergraph(
            period=self.period,
            sources=tuple(sorted(sources.values(), key=lambda item: item.id)),
            computations=tuple(computations),
            sinks=tuple(sorted(self.sinks, key=lambda item: item.id)),
            arcs=tuple(self.arcs),
        )
        graph.validate_placement(graph.asap_placement())
        graph.validate_placement(graph.alap_placement())
        return graph


def build_temporal_hypergraph(
    module: CircuitModule,
    timing: StateTimingPlan,
    *,
    sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
) -> TemporalHypergraph:
    """Build the periodic state-cone temporal hypergraph before physical phase delays exist."""

    if not isinstance(sampling_policy, SamplingPolicy):
        raise TypeError("sampling_policy must be a SamplingPolicy")
    return _TemporalHypergraphBuilder(module, timing, sampling_policy).build()


def format_temporal_hypergraph(graph: TemporalHypergraph) -> str:
    """Render mobility and structural-lifetime statistics for the two extreme placements."""

    source_modes = Counter(item.mode.value for item in graph.sources)
    scalar = sum(item.shape is PayloadShape.SCALAR for item in graph.computations)
    vector = len(graph.computations) - scalar
    movable = [item for item in graph.computations if item.mobility > 0]
    bus_candidates = sum(item.delay_bus_eligible for item in graph.computations)
    asap = graph.transport_cost(graph.asap_placement())
    alap = graph.transport_cost(graph.alap_placement())

    modes = ", ".join(
        f"{name}={count}" for name, count in sorted(source_modes.items())
    ) or "none"
    return "\n".join(
        [
            "temporal computation hypergraph (periodic state cone)",
            (
                f"  period={graph.period}; computations={len(graph.computations)} "
                f"(scalar={scalar}, vector={vector}); sinks={len(graph.sinks)}; "
                f"arcs={len(graph.arcs)}"
            ),
            f"  sources: total={len(graph.sources)}; {modes}",
            (
                f"  mobility: movable={len(movable)}; "
                f"total_slack={sum(item.mobility for item in movable)}; "
                f"max_slack={max((item.mobility for item in movable), default=0)}"
            ),
            f"  scalar delay-bus candidates before settling filter={bus_candidates}",
            (
                "  ASAP structural serial-lifetime upper bound: "
                f"scalar={asap.scalar_serial}; vector={asap.vector_serial}; "
                f"total={asap.total_serial}; "
                f"bus_candidate_scalar={asap.bus_eligible_scalar_serial}"
            ),
            (
                "  ALAP structural serial-lifetime upper bound: "
                f"scalar={alap.scalar_serial}; vector={alap.vector_serial}; "
                f"total={alap.total_serial}; "
                f"bus_candidate_scalar={alap.bus_eligible_scalar_serial}"
            ),
        ]
    )


__all__ = [
    "TemporalArc",
    "TemporalComputation",
    "TemporalHypergraph",
    "TemporalPlacement",
    "TemporalPlacementError",
    "TemporalSink",
    "TemporalSource",
    "TemporalSourceMode",
    "TemporalTransportCost",
    "TransportInterval",
    "build_temporal_hypergraph",
    "format_temporal_hypergraph",
]
