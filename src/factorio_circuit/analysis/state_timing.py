"""Abstract timing analysis for stateful stream components.

The analysis separates semantic state-boundary ordering from physical Factorio phase.  The current
vector registers each describe one compound transition per invocation.  Multiple accumulator adds
belong to the same transition and commute.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    Input,
    InputSample,
    ScalarValue,
    Select,
    VectorConstant,
    VectorInput,
    VectorInputSample,
    VectorSignal,
    VectorValue,
)
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    AccumulatorRegister,
    FreezeRegister,
    FreezeSet,
    StateOperation,
    StateRegister,
    VectorRegisterRead,
)


class StateTimingError(ValueError):
    """Raised when strict state timing cannot be realized by the current state model."""


@dataclass(frozen=True, slots=True)
class StateReadTiming:
    read: VectorRegisterRead
    physical_phase: int


@dataclass(frozen=True, slots=True)
class RegisterTiming:
    register: StateRegister
    commit_offset: int
    state_phase: int
    transition_input_phase: int
    earliest_transition_input_phase: int
    first_update_order: int
    last_update_order: int
    reads: tuple[StateReadTiming, ...]

    def phase_for_read(self, read: VectorRegisterRead) -> int:
        for item in self.reads:
            if item.read is read:
                return item.physical_phase
        raise KeyError(read)


@dataclass(frozen=True, slots=True)
class StateTimingPlan:
    registers: tuple[RegisterTiming, ...]

    def for_register(self, register: StateRegister) -> RegisterTiming:
        for item in self.registers:
            if item.register == register:
                return item
        raise KeyError(register)

    def for_read(self, read: VectorRegisterRead) -> StateReadTiming:
        timing = self.for_register(read.register)
        for item in timing.reads:
            if item.read is read:
                return item
        raise KeyError(read)


@dataclass(frozen=True, slots=True)
class _RegisterSpec:
    register: StateRegister
    operations: tuple[StateOperation, ...]
    reads: tuple[VectorRegisterRead, ...]
    commit_offset: int
    first_update_order: int
    last_update_order: int
    absolute_requirement: int
    state_dependencies: tuple[tuple[StateRegister, int], ...]


def analyze_state_timing(module: CircuitModule) -> StateTimingPlan:
    """Solve semantic commit windows and physical phases for the current vector state.

    State-to-state vector feeds produce ordinary difference constraints between register phases.
    Zero-weight feedback cycles are valid (and are important for mutually coupled registers); a
    positive-weight cycle is rejected because it would require a value to arrive before itself.
    """

    reads = _collect_state_reads(module)
    specs: list[_RegisterSpec] = []
    for register in module.state_registers:
        operations = tuple(op for op in module.state_operations if op.register == register)
        register_reads = tuple(read for read in reads if read.register == register)
        specs.append(_analyze_register_semantics(register, operations, register_reads))

    phases = _solve_state_phases(specs)
    timings: list[RegisterTiming] = []
    for spec in specs:
        state_phase = phases[spec.register.name]
        earliest = spec.absolute_requirement
        for source, offset in spec.state_dependencies:
            earliest = max(earliest, phases[source.name] + offset)
        transition = state_phase + spec.commit_offset
        if transition < earliest:  # pragma: no cover - solver invariant
            raise AssertionError("state phase solver violated an update-input requirement")
        read_timings = tuple(
            StateReadTiming(read, state_phase + read.offset)
            for read in sorted(spec.reads, key=lambda item: item.order)
        )
        timings.append(
            RegisterTiming(
                register=spec.register,
                commit_offset=spec.commit_offset,
                state_phase=state_phase,
                transition_input_phase=transition,
                earliest_transition_input_phase=earliest,
                first_update_order=spec.first_update_order,
                last_update_order=spec.last_update_order,
                reads=read_timings,
            )
        )
    return StateTimingPlan(tuple(timings))


def earliest_scalar_phase(value: ScalarValue) -> int:
    """Earliest phase of scalar logic that has no state-read dependency."""

    memo: dict[int, int] = {}

    def visit(item: ScalarValue) -> int:
        cached = memo.get(id(item))
        if cached is not None:
            return cached
        if isinstance(item, Input):
            result = 0
        elif isinstance(item, InputSample):
            result = item.offset
        elif isinstance(item, Constant):
            result = 0
        elif isinstance(item, VectorSignal):
            result = earliest_vector_phase(item.vector)
        elif isinstance(item, (BinaryOp, Compare)):
            result = max(visit(item.left), visit(item.right)) + 1
        elif isinstance(item, Select):
            false_phase = visit(item.when_false)
            diff_phase = max(visit(item.when_true), false_phase) + 1
            gated_phase = max(diff_phase, visit(item.condition)) + 1
            result = max(false_phase, gated_phase) + 1
        else:  # pragma: no cover
            raise TypeError(item)
        memo[id(item)] = result
        return result

    return visit(value)


def _normalized_when_phase(value: ScalarValue) -> int:
    if isinstance(value, Constant):
        return 0
    return earliest_scalar_phase(value) + 1


def earliest_vector_phase(value: VectorValue) -> int:
    """Earliest absolute phase for vector values with no state dependency.

    Derived runtime-open vector operations contribute their physical one-tick latency.  Values
    depending on register reads are solved only in the full timing plan because their absolute
    phase is relative to another register.
    """

    absolute, dependencies = _vector_requirements(value)
    if dependencies:
        raise StateTimingError("state-dependent vector phases require analyze_state_timing(...)")
    return absolute


def _delay_vector_requirements(
    requirements: tuple[int, tuple[tuple[StateRegister, int], ...]],
    ticks: int,
) -> tuple[int, tuple[tuple[StateRegister, int], ...]]:
    absolute, dependencies = requirements
    return absolute + ticks, tuple((register, offset + ticks) for register, offset in dependencies)


def _vector_requirements(value: object) -> tuple[int, tuple[tuple[StateRegister, int], ...]]:
    from factorio_circuit.frontend import _VectorBinaryOp, _VectorFilter, _VectorScalarOp

    if isinstance(value, (VectorInput, VectorConstant)):
        return 0, ()
    if isinstance(value, VectorInputSample):
        return value.offset, ()
    if isinstance(value, VectorRegisterRead):
        return 0, ((value.register, value.offset),)
    if isinstance(value, _VectorBinaryOp):
        left_abs, left_dependencies = _vector_requirements(value.left)
        right_abs, right_dependencies = _vector_requirements(value.right)
        return (
            max(left_abs, right_abs) + 1,
            tuple(
                (register, offset + 1)
                for register, offset in (*left_dependencies, *right_dependencies)
            ),
        )
    if isinstance(value, _VectorScalarOp):
        vector_abs, dependencies = _vector_requirements(value.vector)
        scalar_phase = earliest_scalar_phase(value.scalar)
        return (
            max(vector_abs, scalar_phase) + 1,
            tuple((register, offset + 1) for register, offset in dependencies),
        )
    if isinstance(value, _VectorFilter):
        return _delay_vector_requirements(_vector_requirements(value.vector), 1)
    raise TypeError(value)


def _analyze_register_semantics(
    register: StateRegister,
    operations: tuple[StateOperation, ...],
    reads: tuple[VectorRegisterRead, ...],
) -> _RegisterSpec:
    if not operations:
        raise StateTimingError(f"state {register.name!r} has no transition operation")

    orders = [op.order for op in operations]
    first_order = min(orders)
    last_order = max(orders)
    before = [read for read in reads if read.order < first_order]
    after = [read for read in reads if read.order > last_order]
    split = [read for read in reads if first_order < read.order < last_order]
    if split:
        orders_text = ", ".join(str(read.order) for read in split)
        raise StateTimingError(
            f"state {register.name!r} has read(s) at order {orders_text} inside one compound "
            "transition; move the read before all update methods or after all of them"
        )

    lower = max((read.offset for read in before), default=0)
    upper_candidates = [read.offset - 1 for read in after]
    upper = min(upper_candidates) if upper_candidates else None
    if upper is not None and lower > upper:
        after_desc = min(after, key=lambda read: read.offset)
        raise StateTimingError(
            f"state {register.name!r} update must occur after logical offset {lower}, but the "
            f"read at order {after_desc.order} observes offset {after_desc.offset}; advance the "
            "freshness cursor before that read"
        )
    commit_offset = lower

    absolute = 0
    dependencies: list[tuple[StateRegister, int]] = []
    if isinstance(register, AccumulatorRegister):
        adds = [op for op in operations if isinstance(op, AccumulatorAdd)]
        clears = [op for op in operations if isinstance(op, AccumulatorClear)]
        unexpected = [
            op for op in operations if not isinstance(op, (AccumulatorAdd, AccumulatorClear))
        ]
        if unexpected:  # pragma: no cover
            raise StateTimingError(f"unexpected operation for AccumulatorReg {register.name!r}")
        if not adds:
            raise StateTimingError(
                f"AccumulatorReg {register.name!r} requires at least one .add(...)"
            )
        if len(clears) > 1:
            raise StateTimingError(f"AccumulatorReg {register.name!r} has multiple clear controls")
        clear_phase = _normalized_when_phase(clears[0].when) if clears else 0
        for add in adds:
            source_abs, source_deps = _vector_requirements(add.value)
            add_phase = _normalized_when_phase(add.when)
            absolute = max(absolute, source_abs, add_phase)
            if clears and not (isinstance(add.when, Constant) and add.when.value != 0):
                absolute = max(absolute, max(add_phase, clear_phase) + 1)
            dependencies.extend(source_deps)
        if clears:
            absolute = max(absolute, clear_phase)
    elif isinstance(register, FreezeRegister):
        freeze_sets: list[FreezeSet] = [op for op in operations if isinstance(op, FreezeSet)]
        freeze_unexpected = [op for op in operations if not isinstance(op, FreezeSet)]
        if freeze_unexpected:  # pragma: no cover
            raise StateTimingError(f"unexpected operation for FreezeReg {register.name!r}")
        if len(freeze_sets) != 1:
            raise StateTimingError(
                f"FreezeReg {register.name!r} requires exactly one .set(data, when=...) call"
            )
        source_abs, source_deps = _vector_requirements(freeze_sets[0].value)
        absolute = max(absolute, source_abs, _normalized_when_phase(freeze_sets[0].when))
        dependencies.extend(source_deps)
    else:  # pragma: no cover
        raise TypeError(register)

    return _RegisterSpec(
        register=register,
        operations=operations,
        reads=reads,
        commit_offset=commit_offset,
        first_update_order=first_order,
        last_update_order=last_order,
        absolute_requirement=absolute,
        state_dependencies=tuple(dependencies),
    )


def _solve_state_phases(specs: list[_RegisterSpec]) -> dict[str, int]:
    phases = {
        spec.register.name: max(0, spec.absolute_requirement - spec.commit_offset) for spec in specs
    }
    by_name = {spec.register.name: spec for spec in specs}

    # P_target >= P_source + read_offset - commit_offset.
    for iteration in range(len(specs)):
        changed = False
        for spec in specs:
            target = spec.register.name
            for source, read_offset in spec.state_dependencies:
                if source.name not in by_name:
                    raise StateTimingError(
                        f"state {target!r} depends on unknown state {source.name!r}"
                    )
                required = phases[source.name] + read_offset - spec.commit_offset
                if required > phases[target]:
                    phases[target] = required
                    changed = True
        if not changed:
            return phases
        if iteration == len(specs) - 1:
            raise StateTimingError(
                "state feedback has positive physical latency around a cycle; the current register "
                "prototypes cannot sustain one logical transition per game tick for this recurrence"
            )
    return phases


def _collect_state_reads(module: CircuitModule) -> tuple[VectorRegisterRead, ...]:
    from factorio_circuit.frontend import _VectorBinaryOp, _VectorFilter, _VectorScalarOp

    result: list[VectorRegisterRead] = []
    seen: set[int] = set()

    def add_vector(value: object) -> bool:
        if isinstance(value, VectorRegisterRead):
            if id(value) not in seen:
                seen.add(id(value))
                result.append(value)
            return True
        if isinstance(value, (VectorInput, VectorInputSample, VectorConstant)):
            return True
        if isinstance(value, _VectorBinaryOp):
            add_vector(value.left)
            add_vector(value.right)
            return True
        if isinstance(value, _VectorScalarOp):
            add_vector(value.vector)
            add_scalar(value.scalar)
            return True
        if isinstance(value, _VectorFilter):
            add_vector(value.vector)
            return True
        return False

    def add_scalar(value: ScalarValue, visited: set[int] | None = None) -> None:
        if visited is None:
            visited = set()
        if id(value) in visited:
            return
        visited.add(id(value))
        if isinstance(value, VectorSignal):
            add_vector(value.vector)
        elif isinstance(value, (BinaryOp, Compare)):
            add_scalar(value.left, visited)
            add_scalar(value.right, visited)
        elif isinstance(value, Select):
            add_scalar(value.condition, visited)
            add_scalar(value.when_true, visited)
            add_scalar(value.when_false, visited)

    for output in module.output.values:
        if not add_vector(output):
            add_scalar(output)
    for op in module.state_operations:
        if isinstance(op, (AccumulatorAdd, FreezeSet)):
            add_vector(op.value)
        if isinstance(op, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
            add_scalar(op.when)
    return tuple(result)
