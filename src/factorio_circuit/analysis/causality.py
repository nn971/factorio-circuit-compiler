"""Target-independent logical causality analysis for state dependencies.

Causality is expressed only in logical occurrence coordinates and structural clock identities.
Physical target latency belongs to later timing/scheduling analysis and is deliberately absent from
the semantic dependency builders. The transitional ``CausalityEdge`` subtype retains the old
latency annotation for compatibility while ``state_timing`` migrates away from constructing logical
graphs itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    ClockId,
    Compare,
    Constant,
    EventScalarFlow,
    EventVectorFlow,
    Input,
    InputSample,
    SampleOn,
    Select,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
)
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    FreezeSet,
    StateOperation,
    StateRegister,
    StateTransition,
    VectorRegisterRead,
    state_transitions,
)


class CausalityEdgeKind(StrEnum):
    """The semantic origin of a state-dependency edge."""

    ORDINARY_STATE_DEPENDENCY = "ordinary_state_dependency"
    EVENT_STATE_DEPENDENCY = "event_state_dependency"


class ClockRelation(StrEnum):
    """Structural relation currently known between dependency endpoint clocks."""

    SAME = "same"
    CROSS = "cross"
    UNKNOWN = "unknown"


class StateOrderError(ValueError):
    """Raised when elaboration order cannot define one logical state-update boundary."""


@dataclass(frozen=True, slots=True)
class LogicalDependency:
    """One ordered state-recurrence dependency in logical occurrence coordinates.

    Clock identities are structural and contract-free. They are keyword-only compatibility fields
    so the original four-position constructor remains valid while Stage 3 gains explicit clock
    relation information.
    """

    source: StateRegister
    target: StateRegister
    kind: CausalityEdgeKind
    logical_displacement: int
    source_clock: ClockId | None = field(default=None, kw_only=True)
    target_clock: ClockId | None = field(default=None, kw_only=True)

    @property
    def clock_relation(self) -> ClockRelation:
        if self.source_clock is None or self.target_clock is None:
            return ClockRelation.UNKNOWN
        if self.source_clock == self.target_clock:
            return ClockRelation.SAME
        return ClockRelation.CROSS


@dataclass(frozen=True, slots=True)
class CausalityEdge(LogicalDependency):
    """Compatibility timing annotation for one logical dependency.

    New causality code should construct :class:`LogicalDependency` directly. ``physical_latency``
    remains here only for compatibility with older timing-oriented tests and callers.
    """

    physical_latency: int

    @property
    def logical(self) -> LogicalDependency:
        """Return the target-independent dependency represented by this timing edge."""

        return LogicalDependency(
            source=self.source,
            target=self.target,
            kind=self.kind,
            logical_displacement=self.logical_displacement,
            source_clock=self.source_clock,
            target_clock=self.target_clock,
        )


@dataclass(frozen=True, slots=True)
class CausalityGraph:
    """An immutable ordered multigraph of target-independent recurrence dependencies."""

    registers: tuple[StateRegister, ...]
    edges: tuple[LogicalDependency, ...]

    def __post_init__(self) -> None:
        register_set = set(self.registers)
        if len(register_set) != len(self.registers):
            raise ValueError("causality graph registers must be unique")
        for edge in self.edges:
            if edge.source not in register_set or edge.target not in register_set:
                raise ValueError("causality edge endpoints must be listed graph registers")


def _operation_kind(operation: StateOperation | StateTransition) -> str:
    if isinstance(operation, StateTransition):
        return operation.kind
    if isinstance(operation, AccumulatorAdd):
        return "add"
    if isinstance(operation, AccumulatorClear):
        return "clear"
    if isinstance(operation, FreezeSet):
        return "set"
    raise TypeError(operation)


def _operation_value(operation: StateOperation | StateTransition) -> object | None:
    if isinstance(operation, StateTransition):
        return operation.value
    if isinstance(operation, (AccumulatorAdd, FreezeSet)):
        return operation.value
    return None


def _operation_when(operation: StateOperation | StateTransition) -> object | None:
    return operation.when


def _register_clock_id(module: CircuitModule, register: StateRegister) -> ClockId | None:
    declared = dict(module.register_clocks).get(register)
    if declared is not None:
        return declared.clock_id
    clocks = {
        transition.clock.clock_id
        for transition in state_transitions(module)
        if transition.register == register
    }
    if len(clocks) == 1:
        return next(iter(clocks))
    return None


def state_read_occurrences(value: object) -> tuple[VectorRegisterRead, ...]:
    """Return state-read leaves in expression occurrence order, preserving duplicates.

    Duplicate leaves are semantically meaningful parallel dependencies even when they refer to the
    same register and logical offset. No physical operation latency is inspected here.
    """

    if isinstance(value, VectorRegisterRead):
        return (value,)
    if isinstance(
        value,
        (
            EventScalarFlow,
            EventVectorFlow,
            Input,
            InputSample,
            Constant,
            VectorInput,
            VectorInputSample,
            VectorConstant,
        ),
    ):
        return ()
    if isinstance(value, SampleOn):
        return state_read_occurrences(value.source)
    if isinstance(value, VectorSignal):
        return state_read_occurrences(value.vector)
    if isinstance(value, (BinaryOp, Compare)):
        return (*state_read_occurrences(value.left), *state_read_occurrences(value.right))
    if isinstance(value, Select):
        return (
            *state_read_occurrences(value.condition),
            *state_read_occurrences(value.when_true),
            *state_read_occurrences(value.when_false),
        )
    if isinstance(value, VectorBinaryOp):
        return (*state_read_occurrences(value.left), *state_read_occurrences(value.right))
    if isinstance(value, VectorScalarOp):
        return (*state_read_occurrences(value.vector), *state_read_occurrences(value.scalar))
    if isinstance(value, (VectorFilter, VectorSelect)):
        return state_read_occurrences(value.vector)
    raise TypeError(value)


def collect_state_reads(
    module: CircuitModule,
    operations: tuple[StateOperation | StateTransition, ...] | None = None,
) -> tuple[VectorRegisterRead, ...]:
    """Collect distinct state-read nodes used by outputs and selected transitions."""

    selected = (
        tuple(
            transition
            for transition in state_transitions(module)
            if transition.trigger is None
        )
        if operations is None
        else operations
    )
    result: list[VectorRegisterRead] = []
    seen: set[int] = set()

    def add(value: object | None) -> None:
        if value is None:
            return
        for read in state_read_occurrences(value):
            if id(read) in seen:
                continue
            seen.add(id(read))
            result.append(read)

    for output in module.output.values:
        add(output)
    for operation in selected:
        kind = _operation_kind(operation)
        if kind in {"add", "set"}:
            add(_operation_value(operation))
        if kind in {"add", "clear", "set"}:
            add(_operation_when(operation))
    return tuple(result)


def infer_commit_offset(
    register: StateRegister,
    operations: tuple[StateOperation | StateTransition, ...],
    reads: tuple[VectorRegisterRead, ...],
) -> int:
    """Infer the logical update boundary for one register from elaboration order alone."""

    if not operations:
        raise StateOrderError(f"state {register.name!r} has no transition operation")

    orders = [operation.order for operation in operations]
    first_order = min(orders)
    last_order = max(orders)
    before = [read for read in reads if read.order < first_order]
    after = [read for read in reads if read.order > last_order]
    split = [read for read in reads if first_order < read.order < last_order]
    if split:
        orders_text = ", ".join(str(read.order) for read in split)
        raise StateOrderError(
            f"state {register.name!r} has read(s) at order {orders_text} inside one compound "
            "transition; move the read before all update methods or after all of them"
        )

    lower = max((read.offset for read in before), default=0)
    upper_candidates = [read.offset - 1 for read in after]
    upper = min(upper_candidates) if upper_candidates else None
    if upper is not None and lower > upper:
        after_desc = min(after, key=lambda read: read.offset)
        raise StateOrderError(
            f"state {register.name!r} update must occur after logical step {lower}, but the "
            f"read at order {after_desc.order} observes step {after_desc.offset}; advance the "
            "logical step before that read"
        )
    return lower


def periodic_causality_graph(
    module: CircuitModule,
    operations: tuple[StateOperation | StateTransition, ...] | None = None,
    registers: tuple[StateRegister, ...] | None = None,
) -> CausalityGraph:
    """Build ordinary state recurrence dependencies directly from semantic IR.

    The displacement of a read ``S[k+r]`` feeding a transition committed between ``k+c`` and
    ``k+c+1`` is ``c + 1 - r``. The graph contains structural clock identities but no target latency
    information.
    """

    selected = (
        tuple(
            transition
            for transition in state_transitions(module)
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
    reads = collect_state_reads(module, selected)
    dependencies: list[LogicalDependency] = []

    for target in active:
        target_operations = tuple(
            operation for operation in selected if operation.register == target
        )
        target_reads = tuple(read for read in reads if read.register == target)
        commit_offset = infer_commit_offset(target, target_operations, target_reads)
        target_clock = _register_clock_id(module, target)
        for operation in target_operations:
            kind = _operation_kind(operation)
            expressions: list[object] = []
            value = _operation_value(operation)
            when = _operation_when(operation)
            if kind in {"add", "set"} and value is not None:
                expressions.append(value)
            if kind in {"add", "clear", "set"} and when is not None:
                expressions.append(when)
            for expression in expressions:
                for read in state_read_occurrences(expression):
                    dependencies.append(
                        LogicalDependency(
                            source=read.register,
                            target=target,
                            kind=CausalityEdgeKind.ORDINARY_STATE_DEPENDENCY,
                            logical_displacement=commit_offset + 1 - read.offset,
                            source_clock=_register_clock_id(module, read.register),
                            target_clock=target_clock,
                        )
                    )

    return CausalityGraph(active, tuple(dependencies))


def event_causality_graph(
    module: CircuitModule,
    transitions: tuple[StateTransition, ...] | None = None,
) -> CausalityGraph:
    """Build Event-driven state recurrence dependencies directly from semantic IR."""

    selected = (
        tuple(
            transition
            for transition in state_transitions(module)
            if transition.trigger is not None
        )
        if transitions is None
        else tuple(transition for transition in transitions if transition.trigger is not None)
    )
    dependencies: list[LogicalDependency] = []
    for transition in selected:
        for expression in (transition.value, transition.when):
            if expression is None:
                continue
            for read in state_read_occurrences(expression):
                dependencies.append(
                    LogicalDependency(
                        source=read.register,
                        target=transition.register,
                        kind=CausalityEdgeKind.EVENT_STATE_DEPENDENCY,
                        logical_displacement=(
                            transition.logical_offset + 1 - read.offset
                        ),
                        source_clock=_register_clock_id(module, read.register),
                        target_clock=transition.clock.clock_id,
                    )
                )
    return CausalityGraph(module.state_registers, tuple(dependencies))


def has_nonpositive_cycle(graph: CausalityGraph) -> bool:
    """Return whether the graph contains a directed cycle of total displacement ``<= 0``.

    A simple cycle has at most ``N`` edges for ``N`` graph registers. Transforming an edge weight
    ``d`` to ``(N + 1) * d - 1`` makes every non-positive-displacement simple cycle negative while
    every positive-displacement simple cycle remains positive. Bellman-Ford then detects such a
    cycle using logical displacement alone.
    """

    if not graph.registers or not graph.edges:
        return False

    index = {register: position for position, register in enumerate(graph.registers)}
    count = len(graph.registers)
    transformed = tuple(
        (
            index[edge.source],
            index[edge.target],
            (count + 1) * edge.logical_displacement - 1,
        )
        for edge in graph.edges
    )

    distances = [0] * count
    for iteration in range(count):
        changed = False
        for source, target, weight in transformed:
            candidate = distances[source] + weight
            if candidate < distances[target]:
                distances[target] = candidate
                changed = True
        if not changed:
            return False
        if iteration == count - 1:
            return True
    return False
