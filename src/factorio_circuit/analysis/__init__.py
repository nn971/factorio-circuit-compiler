"""Compiler analyses."""

from contextvars import ContextVar, Token
from typing import Any, cast

from factorio_circuit.ir.semantic import CircuitModule, ScalarValue
from factorio_circuit.ir.state import (
    StateOperation,
    StateRegister,
    StateTransition,
    VectorRegisterRead,
)

from . import causality as _causality
from . import state_timing as _state_timing
from .causality import (
    CausalityAnalysis,
    CausalityEdge,
    CausalityEdgeKind,
    CausalityGraph,
    ClockRelation,
    LogicalDependency,
    StateOrderError,
    analyze_causality,
    event_causality_graph,
    has_nonpositive_cycle,
    infer_commit_offset,
    periodic_causality_graph,
    state_read_occurrences,
)
from .latency import FACTORIO_LATENCY, TargetLatencyModel
from .phase_delay_census import (
    PhaseDelayCensus,
    PhaseDelayComponent,
    census_phase_delays,
    format_phase_delay_census,
)
from .physical_census import (
    AbstractPhysicalCensus,
    census_abstract_physical,
    format_abstract_physical_census,
)
from .state_timing import (
    ClockDomainTiming,
    EventClockTiming,
    RegisterTiming,
    StateReadTiming,
    StateTimingError,
    StateTimingPlan,
    UnsupportedClockCrossing,
    analyze_clocked_timing,
    analyze_normalized_state_timing,
    analyze_state_timing,
    earliest_scalar_phase,
    earliest_vector_phase,
    validate_event_throughput,
)
from .temporal_alignment import (
    ExactTransportDemand,
    TemporalAlignmentAnalysis,
    TemporalAlignmentDemand,
    TemporalAlignmentKind,
    TemporalAvailability,
    TemporalAvailabilityKind,
    analyze_temporal_alignment,
)
from .temporal_builder import build_temporal_hypergraph
from .temporal_hypergraph import (
    TemporalArc,
    TemporalComputation,
    TemporalHypergraph,
    TemporalPlacement,
    TemporalPlacementError,
    TemporalSink,
    TemporalSource,
    TemporalSourceMode,
    TemporalTransportCost,
    TransportInterval,
    format_temporal_hypergraph,
)
from .temporal_optimize import (
    DelayBusLane,
    DelayBusPlan,
    LiveSourceObservation,
    TemporalOptimizationResult,
    format_temporal_optimization,
    optimize_temporal_hypergraph,
)
from .transport_optimize import (
    SharedTransportBus,
    SharedTransportLane,
    TransportOptimizationResult,
    format_transport_optimization,
    optimize_exact_transports,
)

_causality_impl = cast(Any, _causality)
_state_timing_impl = cast(Any, _state_timing)

# Timing only needs distinct state-read nodes and the strongest physical requirement for a given
# source/logical-offset pair. The public causality API deliberately retains occurrence multiplicity,
# but expanding those occurrences before timing analysis turns a shared expression DAG back into an
# exponentially large tree. Keep these compiler-internal views DAG-aware without changing that
# public occurrence contract.


def _distinct_state_reads(value: object) -> tuple[VectorRegisterRead, ...]:
    result: list[VectorRegisterRead] = []
    seen_nodes: set[int] = set()
    seen_reads: set[int] = set()

    def visit(item: object) -> None:
        node_id = id(item)
        if node_id in seen_nodes:
            return
        seen_nodes.add(node_id)

        if isinstance(item, _causality_impl.VectorRegisterRead):
            if node_id not in seen_reads:
                seen_reads.add(node_id)
                result.append(item)
            return
        if isinstance(
            item,
            (
                _causality_impl.EventScalarFlow,
                _causality_impl.EventVectorFlow,
                _causality_impl.Input,
                _causality_impl.InputSample,
                _causality_impl.Constant,
                _causality_impl.VectorInput,
                _causality_impl.VectorInputSample,
                _causality_impl.VectorConstant,
            ),
        ):
            return
        if isinstance(item, _causality_impl.SampleOn):
            visit(item.source)
            return
        if isinstance(item, _causality_impl.VectorSignal):
            visit(item.vector)
            return
        if isinstance(item, (_causality_impl.BinaryOp, _causality_impl.Compare)):
            visit(item.left)
            visit(item.right)
            return
        if isinstance(item, _causality_impl.Select):
            visit(item.condition)
            visit(item.when_true)
            visit(item.when_false)
            return
        if isinstance(item, _causality_impl.VectorBinaryOp):
            visit(item.left)
            visit(item.right)
            return
        if isinstance(item, _causality_impl.VectorScalarOp):
            visit(item.vector)
            visit(item.scalar)
            return
        if isinstance(item, (_causality_impl.VectorFilter, _causality_impl.VectorSelect)):
            visit(item.vector)
            return
        raise TypeError(item)

    visit(value)
    return tuple(result)


def _dag_collect_state_reads(
    module: CircuitModule,
    operations: tuple[StateOperation | StateTransition, ...] | None = None,
) -> tuple[VectorRegisterRead, ...]:
    selected = (
        tuple(
            transition
            for transition in _causality_impl.state_transitions(module)
            if transition.trigger is None
        )
        if operations is None
        else operations
    )
    result: list[VectorRegisterRead] = []
    seen_reads: set[int] = set()

    def add(value: object | None) -> None:
        if value is None:
            return
        for read in _distinct_state_reads(value):
            read_id = id(read)
            if read_id in seen_reads:
                continue
            seen_reads.add(read_id)
            result.append(read)

    for output in module.output.values:
        add(output)
    for operation in selected:
        kind = _causality_impl._operation_kind(operation)
        if kind in {"add", "set"}:
            add(_causality_impl._operation_value(operation))
        if kind in {"add", "clear", "set"}:
            add(_causality_impl._operation_when(operation))
    return tuple(result)


_original_scalar_requirements = _state_timing._scalar_requirements
_original_vector_requirements = _state_timing._vector_requirements
_requirement_memo: ContextVar[
    dict[tuple[str, int], tuple[_state_timing._Requirement, ...]] | None
] = ContextVar("factorio_circuit_timing_requirement_memo", default=None)


def _compact_requirements(
    requirements: tuple[_state_timing._Requirement, ...],
) -> tuple[_state_timing._Requirement, ...]:
    result: list[_state_timing._Requirement] = []
    positions: dict[tuple[StateRegister | None, int], int] = {}
    for requirement in requirements:
        key = (requirement.source, requirement.logical_offset)
        position = positions.get(key)
        if position is None:
            positions[key] = len(result)
            result.append(requirement)
        elif requirement.latency > result[position].latency:
            result[position] = requirement
    return tuple(result)


def _memoized_requirements(
    kind: str,
    value: object,
) -> tuple[_state_timing._Requirement, ...]:
    memo = _requirement_memo.get()
    token: Token[dict[tuple[str, int], tuple[_state_timing._Requirement, ...]] | None] | None = None
    if memo is None:
        memo = {}
        token = _requirement_memo.set(memo)

    key = (kind, id(value))
    try:
        cached = memo.get(key)
        if cached is not None:
            return cached
        if kind == "scalar":
            result = _compact_requirements(
                _original_scalar_requirements(value)  # type: ignore[arg-type]
            )
        else:
            result = _compact_requirements(_original_vector_requirements(value))
        memo[key] = result
        return result
    finally:
        if token is not None:
            _requirement_memo.reset(token)


def _memoized_scalar_requirements(
    value: ScalarValue,
) -> tuple[_state_timing._Requirement, ...]:
    return _memoized_requirements("scalar", value)


def _memoized_vector_requirements(value: object) -> tuple[_state_timing._Requirement, ...]:
    return _memoized_requirements("vector", value)


def _timing_periodic_causality_graph(
    module: CircuitModule,
    operations: tuple[StateOperation | StateTransition, ...] | None = None,
    registers: tuple[StateRegister, ...] | None = None,
) -> CausalityGraph:
    selected = (
        tuple(
            transition
            for transition in _causality_impl.state_transitions(module)
            if transition.trigger is None
        )
        if operations is None
        else operations
    )
    active = (
        tuple(
            register
            for register in module.state_registers
            if any(operation.register == register for operation in selected)
        )
        if registers is None
        else registers
    )
    reads = _dag_collect_state_reads(module, selected)
    dependencies: list[LogicalDependency] = []
    seen_dependencies: set[tuple[int, int, int]] = set()

    for target in active:
        target_operations = tuple(
            operation for operation in selected if operation.register == target
        )
        target_reads = tuple(read for read in reads if read.register == target)
        commit_offset = _causality_impl.infer_commit_offset(target, target_operations, target_reads)
        target_clock = _causality_impl._register_clock_id(module, target)
        for operation in target_operations:
            kind = _causality_impl._operation_kind(operation)
            expressions: list[object] = []
            value = _causality_impl._operation_value(operation)
            when = _causality_impl._operation_when(operation)
            if kind in {"add", "set"} and value is not None:
                expressions.append(value)
            if kind in {"add", "clear", "set"} and when is not None:
                expressions.append(when)
            for expression in expressions:
                for read in _distinct_state_reads(expression):
                    displacement = commit_offset + 1 - read.offset
                    key = (id(read), id(target), displacement)
                    if key in seen_dependencies:
                        continue
                    seen_dependencies.add(key)
                    dependencies.append(
                        LogicalDependency(
                            source=read.register,
                            target=target,
                            kind=CausalityEdgeKind.ORDINARY_STATE_DEPENDENCY,
                            logical_displacement=displacement,
                            source_clock=_causality_impl._register_clock_id(module, read.register),
                            target_clock=target_clock,
                        )
                    )

    return CausalityGraph(active, tuple(dependencies))


_causality_impl.collect_state_reads = _dag_collect_state_reads
_state_timing_impl.collect_state_reads = _dag_collect_state_reads
_state_timing_impl._scalar_requirements = _memoized_scalar_requirements
_state_timing_impl._vector_requirements = _memoized_vector_requirements
_state_timing_impl.periodic_causality_graph = _timing_periodic_causality_graph
collect_state_reads = _dag_collect_state_reads


__all__ = [
    "AbstractPhysicalCensus",
    "CausalityAnalysis",
    "CausalityEdge",
    "CausalityEdgeKind",
    "CausalityGraph",
    "ClockDomainTiming",
    "ClockRelation",
    "DelayBusLane",
    "DelayBusPlan",
    "EventClockTiming",
    "ExactTransportDemand",
    "FACTORIO_LATENCY",
    "LiveSourceObservation",
    "LogicalDependency",
    "PhaseDelayCensus",
    "PhaseDelayComponent",
    "RegisterTiming",
    "SharedTransportBus",
    "SharedTransportLane",
    "StateOrderError",
    "StateReadTiming",
    "StateTimingError",
    "StateTimingPlan",
    "TargetLatencyModel",
    "TemporalAlignmentAnalysis",
    "TemporalAlignmentDemand",
    "TemporalAlignmentKind",
    "TemporalArc",
    "TemporalAvailability",
    "TemporalAvailabilityKind",
    "TemporalComputation",
    "TemporalHypergraph",
    "TemporalOptimizationResult",
    "TemporalPlacement",
    "TemporalPlacementError",
    "TemporalSink",
    "TemporalSource",
    "TemporalSourceMode",
    "TemporalTransportCost",
    "TransportInterval",
    "TransportOptimizationResult",
    "UnsupportedClockCrossing",
    "analyze_causality",
    "analyze_clocked_timing",
    "analyze_normalized_state_timing",
    "analyze_state_timing",
    "analyze_temporal_alignment",
    "build_temporal_hypergraph",
    "census_abstract_physical",
    "census_phase_delays",
    "collect_state_reads",
    "earliest_scalar_phase",
    "earliest_vector_phase",
    "event_causality_graph",
    "format_abstract_physical_census",
    "format_phase_delay_census",
    "format_temporal_hypergraph",
    "format_temporal_optimization",
    "format_transport_optimization",
    "has_nonpositive_cycle",
    "infer_commit_offset",
    "optimize_exact_transports",
    "optimize_temporal_hypergraph",
    "periodic_causality_graph",
    "state_read_occurrences",
    "validate_event_throughput",
]
