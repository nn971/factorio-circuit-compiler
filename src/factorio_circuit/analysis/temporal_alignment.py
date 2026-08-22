"""Classify periodic Level alignment before exact transport is planned.

A physical phase gap is not automatically a delay requirement.  For one placed temporal
hypergraph, this analysis distinguishes three physical availability modes:

* ``STABLE``: the same semantic token is already present through a validity window;
* ``OBSERVABLE``: the physical Level keeps tracking fresh values and may intentionally be observed
  later under the configured freshness policy;
* ``EXACT``: one chosen token is available only at its current point unless explicitly transported.

Per-use alignment then becomes ``REUSE``, ``OBSERVE_AT`` or ``TRANSPORT_TO``. Exact transport
starts
at the latest phase that is free under the producer's availability proof.  This is the analysis a
shared delay-bus planner should consume: only resulting exact-transport demands require hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from factorio_circuit.ir.semantic import PayloadShape, Select, VectorSignal

from .latency import FACTORIO_LATENCY
from .temporal_hypergraph import (
    TemporalArc,
    TemporalComputation,
    TemporalHypergraph,
    TemporalPlacement,
    TemporalPlacementError,
    TemporalSourceMode,
)


class TemporalAvailabilityKind(StrEnum):
    """How one physical representation may be used after its placed phase."""

    STABLE = "stable"
    OBSERVABLE = "observable"
    EXACT = "exact"


class TemporalAlignmentKind(StrEnum):
    """Physical action needed for one producer use."""

    REUSE = "reuse"
    OBSERVE_AT = "observe-at"
    TRANSPORT_TO = "transport-to"


@dataclass(frozen=True, slots=True)
class TemporalAvailability:
    node: int
    label: str
    shape: PayloadShape
    kind: TemporalAvailabilityKind
    start_phase: int
    end_phase_exclusive: int | None

    def contains(self, phase: int) -> bool:
        return phase >= self.start_phase and (
            self.end_phase_exclusive is None or phase < self.end_phase_exclusive
        )

    @property
    def last_free_phase(self) -> int | None:
        if self.end_phase_exclusive is None:
            return None
        return self.end_phase_exclusive - 1


@dataclass(frozen=True, slots=True)
class TemporalAlignmentDemand:
    producer: int
    consumer: int
    label: str
    shape: PayloadShape
    phase: int
    kind: TemporalAlignmentKind
    transport_start_phase: int | None = None

    @property
    def transport_length(self) -> int:
        if self.kind is not TemporalAlignmentKind.TRANSPORT_TO:
            return 0
        if self.transport_start_phase is None:  # pragma: no cover - dataclass invariant
            raise AssertionError("transport demand has no start phase")
        return self.phase - self.transport_start_phase


@dataclass(frozen=True, slots=True)
class ExactTransportDemand:
    """One shareable chosen-token lifetime after free alignment has been exhausted."""

    producer: int
    label: str
    shape: PayloadShape
    start_phase: int
    end_phase: int
    consumers: tuple[int, ...]
    tap_phases: tuple[int, ...] = ()

    @property
    def length(self) -> int:
        return self.end_phase - self.start_phase

    @property
    def scalar_bus_candidate(self) -> bool:
        return self.shape is PayloadShape.SCALAR and self.length >= 3

    @property
    def long_tap_phases(self) -> tuple[int, ...]:
        """Taps that can leave an isolated shared trunk through a final one-tick egress."""

        return tuple(phase for phase in self.tap_phases if phase >= self.start_phase + 2)

    @property
    def has_one_tick_tap(self) -> bool:
        """Whether a private one-tick branch is still needed alongside any shared bus ingress."""

        return self.start_phase + 1 in self.tap_phases


@dataclass(frozen=True, slots=True)
class TemporalAlignmentAnalysis:
    availabilities: tuple[TemporalAvailability, ...]
    uses: tuple[TemporalAlignmentDemand, ...]
    transports: tuple[ExactTransportDemand, ...]

    def availability_for(self, node: int) -> TemporalAvailability:
        for availability in self.availabilities:
            if availability.node == node:
                return availability
        raise KeyError(node)


def _normalized_semantic(value: object) -> object:
    return value.vector if isinstance(value, VectorSignal) else value


def _consumer_input_phase(
    arc: TemporalArc,
    *,
    phases: dict[int, int],
    computations: dict[int, TemporalComputation],
    semantics: dict[int, object],
    sink_phases: dict[int, int],
) -> int:
    consumer = computations.get(arc.consumer)
    if consumer is None:
        return sink_phases[arc.consumer]

    latency = arc.latency
    if isinstance(consumer.semantic, Select):
        condition = _normalized_semantic(consumer.semantic.condition)
        if semantics[arc.producer] is condition:
            latency = FACTORIO_LATENCY.operation_latency("select_condition", consumer.semantic.name)
    return phases[arc.consumer] - latency


def _source_availability(graph: TemporalHypergraph) -> dict[int, TemporalAvailability]:
    result: dict[int, TemporalAvailability] = {}
    for source in graph.sources:
        if source.mode is TemporalSourceMode.STABLE:
            kind = TemporalAvailabilityKind.STABLE
            end = source.end_phase_exclusive
        elif source.mode is TemporalSourceMode.LIVE:
            kind = TemporalAvailabilityKind.OBSERVABLE
            end = source.end_phase_exclusive
        else:
            kind = TemporalAvailabilityKind.EXACT
            # An exact source denotes one chosen tick even if an older compatibility object carries
            # a wider bookkeeping interval.  Further lifetime must come from exact transport.
            end = source.start_phase + 1
        result[source.id] = TemporalAvailability(
            node=source.id,
            label=source.label,
            shape=source.shape,
            kind=kind,
            start_phase=source.start_phase,
            end_phase_exclusive=end,
        )
    return result


def _derive_computation_availability(
    graph: TemporalHypergraph,
    placement: TemporalPlacement,
    availabilities: dict[int, TemporalAvailability],
    computations: dict[int, TemporalComputation],
    semantics: dict[int, object],
) -> None:
    phases = dict(placement.phases)
    incoming: dict[int, list[TemporalArc]] = {}
    for arc in graph.arcs:
        if arc.consumer in computations:
            incoming.setdefault(arc.consumer, []).append(arc)

    for computation in graph.computations:
        output_phase = phases[computation.id]
        dependencies = incoming.get(computation.id, ())
        child_kinds: list[TemporalAvailabilityKind] = []
        remaining_spans: list[int] = []
        forced_exact = False

        for arc in dependencies:
            child = availabilities[arc.producer]
            input_phase = _consumer_input_phase(
                arc,
                phases=phases,
                computations=computations,
                semantics=semantics,
                sink_phases={},
            )
            if input_phase < child.start_phase:
                raise TemporalPlacementError(
                    f"computation {computation.label!r} requests {child.label!r} at phase "
                    f"{input_phase} before it becomes available at {child.start_phase}"
                )
            if child.kind is TemporalAvailabilityKind.EXACT or not child.contains(input_phase):
                # The child must first be exact-transported to this input boundary.  That freezes
                # the downstream result at the placed output phase too.
                forced_exact = True
                continue
            child_kinds.append(child.kind)
            if child.end_phase_exclusive is not None:
                remaining_spans.append(child.end_phase_exclusive - input_phase)

        # Scalar Select still has implementation-dependent internal timing in the production
        # lowerer.  Until its actual chosen implementation exports a freshness proof, treating the
        # Select result as re-observable would let the transport planner erase hardware that the
        # lowerer may still need.  Keep this boundary exact; ordinary BinaryOp/Compare and supported
        # vector feed-forward operations can continue propagating OBSERVABLE availability.
        if isinstance(computation.semantic, Select):
            forced_exact = True

        end: int | None
        if forced_exact:
            kind = TemporalAvailabilityKind.EXACT
            end = output_phase + 1
        elif TemporalAvailabilityKind.OBSERVABLE in child_kinds:
            kind = TemporalAvailabilityKind.OBSERVABLE
            span = min(remaining_spans) if remaining_spans else None
            end = None if span is None else output_phase + span
        else:
            kind = TemporalAvailabilityKind.STABLE
            span = min(remaining_spans) if remaining_spans else None
            end = None if span is None else output_phase + span

        availabilities[computation.id] = TemporalAvailability(
            node=computation.id,
            label=computation.label,
            shape=computation.shape,
            kind=kind,
            start_phase=output_phase,
            end_phase_exclusive=end,
        )


def analyze_temporal_alignment(
    graph: TemporalHypergraph,
    placement: TemporalPlacement,
) -> TemporalAlignmentAnalysis:
    """Classify free observation/reuse and residual exact transport for one placement."""

    graph.validate_placement(placement)
    phases = dict(placement.phases)
    computations = {item.id: item for item in graph.computations}
    semantics: dict[int, object] = {item.id: item.semantic for item in graph.sources}
    semantics.update({item.id: item.semantic for item in graph.computations})
    sink_phases = {sink.id: sink.phase for sink in graph.sinks}

    availabilities = _source_availability(graph)
    _derive_computation_availability(
        graph,
        placement,
        availabilities,
        computations,
        semantics,
    )

    uses: list[TemporalAlignmentDemand] = []
    grouped_transports: dict[tuple[int, int], list[TemporalAlignmentDemand]] = {}

    for arc in graph.arcs:
        producer = availabilities[arc.producer]
        phase = _consumer_input_phase(
            arc,
            phases=phases,
            computations=computations,
            semantics=semantics,
            sink_phases=sink_phases,
        )
        if phase < producer.start_phase:
            raise TemporalPlacementError(
                f"consumer requests {producer.label!r} at phase {phase} before its availability "
                f"starts at {producer.start_phase}"
            )

        if producer.contains(phase):
            if producer.kind is TemporalAvailabilityKind.OBSERVABLE:
                kind = TemporalAlignmentKind.OBSERVE_AT
            else:
                kind = TemporalAlignmentKind.REUSE
            demand = TemporalAlignmentDemand(
                producer=arc.producer,
                consumer=arc.consumer,
                label=producer.label,
                shape=arc.shape,
                phase=phase,
                kind=kind,
            )
            uses.append(demand)
            continue

        start = producer.last_free_phase
        if start is None:  # pragma: no cover - unbounded availability contains every later phase
            raise AssertionError("unbounded availability unexpectedly requires transport")
        if phase <= start:  # pragma: no cover - contains() would have handled this
            raise AssertionError("exact transport must advance physical time")
        demand = TemporalAlignmentDemand(
            producer=arc.producer,
            consumer=arc.consumer,
            label=producer.label,
            shape=producer.shape,
            phase=phase,
            kind=TemporalAlignmentKind.TRANSPORT_TO,
            transport_start_phase=start,
        )
        uses.append(demand)
        grouped_transports.setdefault((arc.producer, start), []).append(demand)

    transports = tuple(
        sorted(
            (
                ExactTransportDemand(
                    producer=producer,
                    label=items[0].label,
                    shape=items[0].shape,
                    start_phase=start,
                    end_phase=max(item.phase for item in items),
                    consumers=tuple(sorted({item.consumer for item in items})),
                    tap_phases=tuple(sorted({item.phase for item in items})),
                )
                for (producer, start), items in grouped_transports.items()
            ),
            key=lambda item: (
                item.start_phase,
                item.end_phase,
                item.shape.value,
                item.producer,
            ),
        )
    )

    return TemporalAlignmentAnalysis(
        availabilities=tuple(sorted(availabilities.values(), key=lambda item: item.node)),
        uses=tuple(uses),
        transports=transports,
    )


__all__ = [
    "ExactTransportDemand",
    "TemporalAlignmentAnalysis",
    "TemporalAlignmentDemand",
    "TemporalAlignmentKind",
    "TemporalAvailability",
    "TemporalAvailabilityKind",
    "analyze_temporal_alignment",
]
