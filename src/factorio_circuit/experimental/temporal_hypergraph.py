"""Experimental phase-free computation hypergraph and temporal materialization oracle.

This module is intentionally disconnected from the canonical compiler pipeline.  It explores a
replacement for eager phase-alignment delays: logical computations are represented without absolute
Factorio phases, then a finite periodic space-time model asks where synthetic identity registers
(materializers) are needed to keep logical tokens available through otherwise irrelevant ticks.

The exact search implemented here is deliberately small-scale.  It is a reference oracle for toy
circuits and future polynomial/ILP solvers, not a production algorithm for Snake.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    ClockId,
    Compare,
    Constant,
    Flow,
    FlowInput,
    FlowInputSample,
    FlowVectorInput,
    FlowVectorInputSample,
    FlowVectorRegisterRead,
    PayloadShape,
    SampleOn,
    Select,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    is_vector_value,
)
from factorio_circuit.ir.state import state_transitions


@dataclass(frozen=True, slots=True)
class TemporalValue:
    """One logical token family; no physical phase or concrete signal is assigned."""

    id: int
    label: str
    shape: PayloadShape
    clock: ClockId | None
    logical_offset: int
    source_kind: str | None = None


@dataclass(frozen=True, slots=True)
class TemporalOperation:
    """One phase-free logical computation hyperedge with per-input target latency."""

    id: int
    inputs: tuple[int, ...]
    output: int
    input_latencies: tuple[int, ...]
    label: str

    def __post_init__(self) -> None:
        if not self.inputs:
            raise ValueError("temporal operation requires at least one input")
        if len(self.inputs) != len(self.input_latencies):
            raise ValueError("temporal operation input/latency lengths disagree")
        if any(latency < 0 for latency in self.input_latencies):
            raise ValueError("temporal operation latencies must be nonnegative")


@dataclass(frozen=True, slots=True)
class TemporalObservation:
    """A semantic use whose physical phase is intentionally still unassigned."""

    value: int
    label: str
    clock: ClockId | None
    logical_offset: int


@dataclass(frozen=True, slots=True)
class TemporalHypergraph:
    """Phase-free logical computation plus unresolved semantic observations."""

    name: str
    values: tuple[TemporalValue, ...]
    operations: tuple[TemporalOperation, ...]
    observations: tuple[TemporalObservation, ...]

    def validate(self) -> None:
        ids = [value.id for value in self.values]
        if len(set(ids)) != len(ids):
            raise ValueError("temporal hypergraph contains duplicate value ids")
        known = set(ids)
        producers: dict[int, int] = {}
        for operation in self.operations:
            if operation.output not in known or any(item not in known for item in operation.inputs):
                raise ValueError("temporal operation references an unknown value")
            previous = producers.setdefault(operation.output, operation.id)
            if previous != operation.id:
                raise ValueError("temporal value has multiple computation producers")
        if any(observation.value not in known for observation in self.observations):
            raise ValueError("temporal observation references an unknown value")

    @property
    def source_values(self) -> tuple[TemporalValue, ...]:
        produced = {operation.output for operation in self.operations}
        return tuple(value for value in self.values if value.id not in produced)

    def summary(self) -> str:
        source_kinds = Counter(value.source_kind or "derived" for value in self.values)
        operation_kinds = Counter(operation.label for operation in self.operations)
        clocks = {value.clock for value in self.values if value.clock is not None}
        lines = [
            "experimental temporal hypergraph",
            f"  values={len(self.values)}; operations={len(self.operations)}; "
            f"observations={len(self.observations)}; clocks={len(clocks)}",
            "  values by source kind:",
        ]
        for label, count in sorted(source_kinds.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"    {label}: {count}")
        lines.append("  operations by kind:")
        for label, count in sorted(operation_kinds.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"    {label}: {count}")
        return "\n".join(lines)


class _SemanticHypergraphBuilder:
    def __init__(self, module: CircuitModule) -> None:
        self.module = module
        self.values: list[TemporalValue] = []
        self.operations: list[TemporalOperation] = []
        self.observations: list[TemporalObservation] = []
        self._memo: dict[int, int] = {}

    def build(self) -> TemporalHypergraph:
        for index, output in enumerate(self.module.output.values):
            value_id = self._visit(output)
            name = self.module.output.names[index] if self.module.output.names else None
            self.observations.append(self._observation(value_id, f"output:{name or index}", output))

        for transition in state_transitions(self.module):
            if transition.trigger is not None:
                raise ValueError("experimental Level hypergraph does not support Event transitions")
            if transition.value is not None:
                value_id = self._visit(transition.value)
                self.observations.append(
                    self._observation(
                        value_id,
                        f"state:{transition.register.name}:{transition.kind}:value",
                        transition.value,
                    )
                )
            if transition.when is not None:
                value_id = self._visit(transition.when)
                self.observations.append(
                    self._observation(
                        value_id,
                        f"state:{transition.register.name}:{transition.kind}:control",
                        transition.when,
                    )
                )

        for operation in self.module.operations:
            self._visit(operation)

        graph = TemporalHypergraph(
            self.module.name,
            tuple(self.values),
            tuple(self.operations),
            tuple(self.observations),
        )
        graph.validate()
        return graph

    def _visit(self, value: object) -> int:
        cached = self._memo.get(id(value))
        if cached is not None:
            return cached

        value_id = len(self.values)
        self._memo[id(value)] = value_id
        self.values.append(
            TemporalValue(
                id=value_id,
                label=self._label(value),
                shape=PayloadShape.VECTOR if is_vector_value(value) else PayloadShape.SCALAR,
                clock=self._clock(value),
                logical_offset=self._logical_offset(value),
                source_kind=self._source_kind(value),
            )
        )

        operation = self._operation(value, value_id)
        if operation is not None:
            self.operations.append(operation)
        return value_id

    def _operation(self, value: object, output: int) -> TemporalOperation | None:
        inputs: tuple[object, ...]
        latencies: tuple[int, ...]
        label: str
        if isinstance(value, BinaryOp):
            inputs = (value.left, value.right)
            latency = FACTORIO_LATENCY.operation_latency("scalar_binary", value.op)
            latencies = (latency, latency)
            label = f"scalar:{value.op}"
        elif isinstance(value, Compare):
            inputs = (value.left, value.right)
            latency = FACTORIO_LATENCY.operation_latency("compare", value.op)
            latencies = (latency, latency)
            label = f"compare:{value.op}"
        elif isinstance(value, Select):
            inputs = (value.condition, value.when_true, value.when_false)
            latencies = (
                FACTORIO_LATENCY.operation_latency("select_condition", value.name),
                FACTORIO_LATENCY.operation_latency("select_data", value.name),
                FACTORIO_LATENCY.operation_latency("select_data", value.name),
            )
            label = "select"
        elif isinstance(value, VectorSignal):
            inputs = (value.vector,)
            latencies = (0,)
            label = "vector-lane-read"
        elif isinstance(value, VectorBinaryOp):
            inputs = (value.left, value.right)
            latency = FACTORIO_LATENCY.operation_latency("vector_binary", value.op)
            latencies = (latency, latency)
            label = f"vector:{value.op}"
        elif isinstance(value, VectorScalarOp):
            inputs = (value.vector, value.scalar)
            latency = FACTORIO_LATENCY.operation_latency("vector_scalar", value.op)
            latencies = (latency, latency)
            label = f"vector-scalar:{value.op}"
        elif isinstance(value, VectorSelect):
            inputs = (value.vector,)
            latencies = (FACTORIO_LATENCY.operation_latency("vector_select", value.op),)
            label = f"vector-select:{value.op}"
        elif isinstance(value, VectorFilter):
            inputs = (value.vector,)
            latencies = (FACTORIO_LATENCY.operation_latency("vector_filter", value.op),)
            label = f"vector-filter:{value.op}"
        elif isinstance(value, SampleOn):
            raise ValueError("experimental Level hypergraph does not support SampleOn")
        else:
            return None

        return TemporalOperation(
            id=len(self.operations),
            inputs=tuple(self._visit(item) for item in inputs),
            output=output,
            input_latencies=latencies,
            label=label,
        )

    def _observation(self, value_id: int, label: str, value: object) -> TemporalObservation:
        return TemporalObservation(
            value=value_id,
            label=label,
            clock=self._clock(value),
            logical_offset=self._logical_offset(value),
        )

    @staticmethod
    def _flow(value: object) -> Flow | None:
        flow = getattr(value, "flow", None)
        return flow if isinstance(flow, Flow) else None

    @classmethod
    def _clock(cls, value: object) -> ClockId | None:
        flow = cls._flow(value)
        return None if flow is None else flow.clock.clock_id

    @classmethod
    def _logical_offset(cls, value: object) -> int:
        flow = cls._flow(value)
        if flow is not None:
            return flow.logical_offset
        return int(getattr(value, "offset", 0))

    @staticmethod
    def _label(value: object) -> str:
        name = getattr(value, "name", None)
        if isinstance(name, str) and name:
            return name
        return type(value).__name__

    @staticmethod
    def _source_kind(value: object) -> str | None:
        if isinstance(value, (FlowInput, FlowVectorInput)):
            return "input"
        if isinstance(value, (FlowInputSample, FlowVectorInputSample)):
            return "input-sample"
        if isinstance(value, FlowVectorRegisterRead):
            return "state-read"
        if isinstance(value, (Constant, VectorConstant)):
            return "constant"
        return None


def build_level_temporal_hypergraph(module: CircuitModule) -> TemporalHypergraph:
    """Build an experimental phase-free graph from an already canonical Level module."""

    return _SemanticHypergraphBuilder(module).build()


@dataclass(frozen=True, slots=True)
class PeriodicDemand:
    """Require one logical value token to be correct at one phase in a fixed period."""

    value: int
    phase: int
    label: str = ""


@dataclass(frozen=True, slots=True)
class Materialization:
    """One scalar synthetic register chosen by the exact reference search."""

    value: int
    capture_phase: int
    valid_from: int
    valid_until: int


@dataclass(frozen=True, slots=True)
class MaterializationPlan:
    """Minimum-cardinality scalar materialization plan for the finite reference model."""

    materializations: tuple[Materialization, ...]
    availability_masks: tuple[int, ...]
    explored_states: int

    @property
    def register_count(self) -> int:
        return len(self.materializations)


def exact_scalar_materializations(
    graph: TemporalHypergraph,
    *,
    period: int,
    source_windows: dict[int, tuple[tuple[int, int], ...]],
    demands: tuple[PeriodicDemand, ...],
    hold_latency: int = 1,
    state_limit: int = 100_000,
) -> MaterializationPlan:
    """Find the exact minimum number of scalar synthetic holds in a fixed-period toy model.

    A bit ``t`` in a value's availability mask means that the physical representation is guaranteed
    to equal the desired logical token at phase ``t``.  Continuous operations propagate correctness
    through their per-input latencies.  A synthetic materializer may capture any currently correct
    value and, after ``hold_latency`` ticks, retain that same token through the end of the period.

    Every materializer has unit cost and holds one value.  Breadth-first search is therefore exact.
    This deliberately omits vector-bank packing, wrap-around uses, Events, and physical memory
    implementation details; it is an oracle for validating future faster/richer algorithms.
    """

    graph.validate()
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("period must be a positive integer")
    if isinstance(hold_latency, bool) or not isinstance(hold_latency, int) or hold_latency < 1:
        raise ValueError("hold_latency must be a positive integer")
    if state_limit < 1:
        raise ValueError("state_limit must be positive")

    value_index = {value.id: index for index, value in enumerate(graph.values)}
    full_mask = (1 << period) - 1
    initial = [0] * len(graph.values)
    for value_id, windows in source_windows.items():
        if value_id not in value_index:
            raise ValueError(f"source window references unknown value {value_id}")
        mask = 0
        for window_start, end in windows:
            if not (0 <= window_start <= end <= period):
                raise ValueError("source windows must lie inside one period")
            if window_start < end:
                mask |= ((1 << (end - window_start)) - 1) << window_start
        initial[value_index[value_id]] |= mask

    for demand in demands:
        if demand.value not in value_index:
            raise ValueError(f"demand references unknown value {demand.value}")
        if not 0 <= demand.phase < period:
            raise ValueError("demand phase must lie inside one period")

    operation_order = tuple(graph.operations)

    def close(state: tuple[int, ...]) -> tuple[int, ...]:
        masks = list(state)
        changed = True
        while changed:
            changed = False
            for operation in operation_order:
                output_index = value_index[operation.output]
                output_mask = masks[output_index]
                for output_phase in range(period):
                    if output_mask & (1 << output_phase):
                        continue
                    usable = True
                    for input_id, latency in zip(
                        operation.inputs, operation.input_latencies, strict=True
                    ):
                        input_phase = output_phase - latency
                        if input_phase < 0:
                            usable = False
                            break
                        input_mask = masks[value_index[input_id]]
                        if not input_mask & (1 << input_phase):
                            usable = False
                            break
                    if usable:
                        output_mask |= 1 << output_phase
                        changed = True
                masks[output_index] = output_mask
        return tuple(mask & full_mask for mask in masks)

    def covered(state: tuple[int, ...]) -> bool:
        return all(state[value_index[demand.value]] & (1 << demand.phase) for demand in demands)

    start = close(tuple(initial))
    if covered(start):
        return MaterializationPlan((), start, 1)

    queue: deque[tuple[int, ...]] = deque([start])
    predecessor: dict[tuple[int, ...], tuple[tuple[int, ...], Materialization] | None] = {
        start: None
    }
    goal: tuple[int, ...] | None = None

    while queue:
        state = queue.popleft()
        if len(predecessor) > state_limit:
            raise RuntimeError(
                f"exact temporal materialization search exceeded {state_limit} states"
            )

        for value in graph.values:
            index = value_index[value.id]
            mask = state[index]
            if mask == 0:
                continue

            capture_phase = _earliest_profitable_capture(mask, period, hold_latency)
            if capture_phase is None:
                continue
            valid_from = capture_phase + hold_latency
            extension = full_mask ^ ((1 << valid_from) - 1)
            extended = mask | extension
            if extended == mask:
                continue

            candidate = list(state)
            candidate[index] = extended
            next_state = close(tuple(candidate))
            if next_state in predecessor:
                continue
            materialization = Materialization(
                value=value.id,
                capture_phase=capture_phase,
                valid_from=valid_from,
                valid_until=period,
            )
            predecessor[next_state] = (state, materialization)
            if covered(next_state):
                goal = next_state
                queue.clear()
                break
            queue.append(next_state)

    if goal is None:
        missing = ", ".join(demand.label or str(demand) for demand in demands)
        raise ValueError(f"periodic demands are not realizable by scalar holds: {missing}")

    chosen: list[Materialization] = []
    cursor = goal
    while predecessor[cursor] is not None:
        previous, materialization = predecessor[cursor]  # type: ignore[misc]
        chosen.append(materialization)
        cursor = previous
    chosen.reverse()
    return MaterializationPlan(tuple(chosen), goal, len(predecessor))


def _earliest_profitable_capture(mask: int, period: int, hold_latency: int) -> int | None:
    """Return the earliest capture that adds future validity; later captures are dominated."""

    for phase in range(period):
        if not mask & (1 << phase):
            continue
        valid_from = phase + hold_latency
        if valid_from >= period:
            continue
        extension = ((1 << period) - 1) ^ ((1 << valid_from) - 1)
        if extension & ~mask:
            return phase
    return None
