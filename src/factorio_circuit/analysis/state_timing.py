"""Logical state timing and physical clock-period inference.

Logical steps and Factorio game ticks are separate coordinates.  Stateless combinators preserve a
logical step while adding physical latency.  Register transitions advance logical state and may need
more than one physical tick per logical step.  The analyzer groups ordinary state dependencies into
clock domains and chooses the smallest feasible physical period for each domain.
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
    """Raised when logical state ordering has no realizable physical schedule."""


@dataclass(frozen=True, slots=True)
class StateReadTiming:
    read: VectorRegisterRead
    physical_phase: int


@dataclass(frozen=True, slots=True)
class ClockDomainTiming:
    """One set of state streams that share a logical-step cadence."""

    id: int
    period: int
    registers: tuple[StateRegister, ...]


@dataclass(frozen=True, slots=True)
class RegisterTiming:
    register: StateRegister
    clock_domain: int
    period: int
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
    domains: tuple[ClockDomainTiming, ...]
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

    def domain_for_register(self, register: StateRegister) -> ClockDomainTiming:
        timing = self.for_register(register)
        for domain in self.domains:
            if domain.id == timing.clock_domain:
                return domain
        raise KeyError(register)

    @property
    def uniform_period(self) -> int | None:
        """Return the common state period, or ``None`` for heterogeneous domains."""

        periods = {domain.period for domain in self.domains}
        if not periods:
            return 1
        if len(periods) == 1:
            return next(iter(periods))
        return None


@dataclass(frozen=True, slots=True)
class _Requirement:
    """Availability of one leaf after a logical displacement and physical latency."""

    source: StateRegister | None
    logical_offset: int
    latency: int


@dataclass(frozen=True, slots=True)
class _RegisterSpec:
    register: StateRegister
    operations: tuple[StateOperation, ...]
    reads: tuple[VectorRegisterRead, ...]
    commit_offset: int
    first_update_order: int
    last_update_order: int
    requirements: tuple[_Requirement, ...]


def analyze_state_timing(module: CircuitModule) -> StateTimingPlan:
    """Infer logical clock domains, their minimal periods, and concrete physical phases.

    For a register with state phase ``phi`` and domain period ``P``, logical state ``S[k]`` is
    observable at physical tick ``phi + k*P``.  A transition committed between logical boundaries
    ``k`` and ``k+1`` receives its physical update input one game tick before the latter boundary.

    Ordinary expressions preserve logical indices.  Therefore any ordinary expression connecting
    state registers places those registers in the same clock domain.  Different domains may still
    share raw external inputs; explicit cross-domain state resampling is intentionally not present yet.
    """

    if not module.state_registers:
        return StateTimingPlan((), ())

    reads = _collect_state_reads(module)
    specs: list[_RegisterSpec] = []
    for register in module.state_registers:
        operations = tuple(op for op in module.state_operations if op.register == register)
        register_reads = tuple(read for read in reads if read.register == register)
        specs.append(_analyze_register_semantics(register, operations, register_reads))

    specs_by_name = {spec.register.name: spec for spec in specs}
    groups = _infer_clock_domain_registers(module)

    domain_timings: list[ClockDomainTiming] = []
    phase_by_name: dict[str, int] = {}
    period_by_name: dict[str, int] = {}
    domain_by_name: dict[str, int] = {}

    for domain_id, registers in enumerate(groups):
        domain_specs = [specs_by_name[register.name] for register in registers]
        period, phases = _solve_domain(domain_specs)
        domain_timings.append(ClockDomainTiming(domain_id, period, registers))
        for register in registers:
            phase_by_name[register.name] = phases[register.name]
            period_by_name[register.name] = period
            domain_by_name[register.name] = domain_id

    timings: list[RegisterTiming] = []
    for spec in specs:
        state_phase = phase_by_name[spec.register.name]
        period = period_by_name[spec.register.name]
        transition = state_phase + (spec.commit_offset + 1) * period - 1
        earliest = _earliest_requirement_phase(spec, period, phase_by_name)
        if transition < earliest:  # pragma: no cover - solver invariant
            raise AssertionError("clock-period solver violated an update-input requirement")
        read_timings = tuple(
            StateReadTiming(read, state_phase + read.offset * period)
            for read in sorted(spec.reads, key=lambda item: item.order)
        )
        timings.append(
            RegisterTiming(
                register=spec.register,
                clock_domain=domain_by_name[spec.register.name],
                period=period,
                commit_offset=spec.commit_offset,
                state_phase=state_phase,
                transition_input_phase=transition,
                earliest_transition_input_phase=earliest,
                first_update_order=spec.first_update_order,
                last_update_order=spec.last_update_order,
                reads=read_timings,
            )
        )

    return StateTimingPlan(tuple(domain_timings), tuple(timings))


def earliest_scalar_phase(value: ScalarValue) -> int:
    """Earliest physical phase for a state-independent scalar in the default ``P=1`` domain."""

    requirements = _scalar_requirements(value)
    if any(item.source is not None for item in requirements):
        raise StateTimingError("state-dependent scalar phases require analyze_state_timing(...)")
    return max((item.logical_offset + item.latency for item in requirements), default=0)


def earliest_vector_phase(value: VectorValue) -> int:
    """Earliest physical phase for a state-independent vector in the default ``P=1`` domain."""

    requirements = _vector_requirements(value)
    if any(item.source is not None for item in requirements):
        raise StateTimingError("state-dependent vector phases require analyze_state_timing(...)")
    return max((item.logical_offset + item.latency for item in requirements), default=0)


def _delay_requirements(
    requirements: tuple[_Requirement, ...], ticks: int
) -> tuple[_Requirement, ...]:
    return tuple(
        _Requirement(item.source, item.logical_offset, item.latency + ticks)
        for item in requirements
    )


def _scalar_requirements(value: ScalarValue) -> tuple[_Requirement, ...]:
    if isinstance(value, Input):
        return (_Requirement(None, 0, 0),)
    if isinstance(value, InputSample):
        return (_Requirement(None, value.offset, 0),)
    if isinstance(value, Constant):
        return (_Requirement(None, 0, 0),)
    if isinstance(value, VectorSignal):
        return _vector_requirements(value.vector)
    if isinstance(value, (BinaryOp, Compare)):
        return _delay_requirements(
            (*_scalar_requirements(value.left), *_scalar_requirements(value.right)), 1
        )
    if isinstance(value, Select):
        # The conservative generic mux is false + (true-false)*condition.  Keep the same timing
        # envelope as the previous analyzer even when physical optimization later fuses the mux.
        return (
            *_delay_requirements(_scalar_requirements(value.when_true), 3),
            *_delay_requirements(_scalar_requirements(value.when_false), 3),
            *_delay_requirements(_scalar_requirements(value.condition), 2),
        )
    raise TypeError(value)


def _control_requirements(value: ScalarValue) -> tuple[_Requirement, ...]:
    """Requirements after nonzero/zero normalization at a state boundary."""

    if isinstance(value, Constant):
        return ()
    return _delay_requirements(_scalar_requirements(value), 1)


def _vector_requirements(value: object) -> tuple[_Requirement, ...]:
    from factorio_circuit.frontend import _VectorBinaryOp, _VectorFilter, _VectorScalarOp

    if isinstance(value, VectorInput):
        return (_Requirement(None, 0, 0),)
    if isinstance(value, VectorInputSample):
        return (_Requirement(None, value.offset, 0),)
    if isinstance(value, VectorConstant):
        return (_Requirement(None, 0, 0),)
    if isinstance(value, VectorRegisterRead):
        return (_Requirement(value.register, value.offset, 0),)
    if isinstance(value, _VectorBinaryOp):
        return _delay_requirements(
            (*_vector_requirements(value.left), *_vector_requirements(value.right)), 1
        )
    if isinstance(value, _VectorScalarOp):
        return _delay_requirements(
            (*_vector_requirements(value.vector), *_scalar_requirements(value.scalar)), 1
        )
    if isinstance(value, _VectorFilter):
        return _delay_requirements(_vector_requirements(value.vector), 1)
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
            f"state {register.name!r} update must occur after logical step {lower}, but the "
            f"read at order {after_desc.order} observes step {after_desc.offset}; advance the "
            "logical step before that read"
        )
    commit_offset = lower

    requirements: list[_Requirement] = []
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

        clear_requirements = _control_requirements(clears[0].when) if clears else ()
        for add in adds:
            requirements.extend(_vector_requirements(add.value))
            requirements.extend(_control_requirements(add.when))
            # A physical state gate can test add-enable and clear-disable in one decider stage.
            requirements.extend(clear_requirements)
        requirements.extend(clear_requirements)

    elif isinstance(register, FreezeRegister):
        freeze_sets = [op for op in operations if isinstance(op, FreezeSet)]
        freeze_unexpected = [op for op in operations if not isinstance(op, FreezeSet)]
        if freeze_unexpected:  # pragma: no cover
            raise StateTimingError(f"unexpected operation for FreezeReg {register.name!r}")
        if len(freeze_sets) != 1:
            raise StateTimingError(
                f"FreezeReg {register.name!r} requires exactly one .set(data, when=...) call"
            )
        requirements.extend(_vector_requirements(freeze_sets[0].value))
        requirements.extend(_control_requirements(freeze_sets[0].when))

    else:  # pragma: no cover
        raise TypeError(register)

    return _RegisterSpec(
        register=register,
        operations=operations,
        reads=reads,
        commit_offset=commit_offset,
        first_update_order=first_order,
        last_update_order=last_order,
        requirements=tuple(requirements),
    )


def _earliest_requirement_phase(
    spec: _RegisterSpec,
    period: int,
    phases: dict[str, int],
) -> int:
    earliest = 0
    for requirement in spec.requirements:
        base = 0 if requirement.source is None else phases[requirement.source.name]
        earliest = max(
            earliest,
            base + requirement.logical_offset * period + requirement.latency,
        )
    return earliest


def _solve_domain(specs: list[_RegisterSpec]) -> tuple[int, dict[str, int]]:
    """Find the smallest integer period whose difference constraints are feasible."""

    state_edges = sum(
        requirement.latency + 1
        for spec in specs
        for requirement in spec.requirements
        if requirement.source is not None
    )
    # Any feasible recurrence cycle has a positive integer logical distance.  Its required period is
    # at most the sum of its positive physical constants, which is bounded by all state edges here.
    max_period = max(1, state_edges)

    for period in range(1, max_period + 1):
        phases = _solve_phases_for_period(specs, period)
        if phases is not None:
            return period, phases

    names = ", ".join(spec.register.name for spec in specs)
    raise StateTimingError(
        "state recurrence has no finite logical clock period: ordinary same-step dependencies form "
        f"a noncausal/zero-distance physical cycle in domain {{{names}}}"
    )


def _solve_phases_for_period(
    specs: list[_RegisterSpec], period: int
) -> dict[str, int] | None:
    phases: dict[str, int] = {}
    names = {spec.register.name for spec in specs}

    for spec in specs:
        lower_bound = 0
        for requirement in spec.requirements:
            if requirement.source is not None:
                continue
            lower_bound = max(
                lower_bound,
                (requirement.logical_offset - spec.commit_offset - 1) * period
                + requirement.latency
                + 1,
            )
        phases[spec.register.name] = lower_bound

    # phi_target >= phi_source + (r-c-1)P + latency + 1.
    for iteration in range(len(specs)):
        changed = False
        for spec in specs:
            target = spec.register.name
            for requirement in spec.requirements:
                source = requirement.source
                if source is None:
                    continue
                if source.name not in names:
                    raise StateTimingError(
                        f"state {target!r} depends on state {source.name!r} in another clock domain; "
                        "ordinary state expressions must share one logical clock domain"
                    )
                required = (
                    phases[source.name]
                    + (requirement.logical_offset - spec.commit_offset - 1) * period
                    + requirement.latency
                    + 1
                )
                if required > phases[target]:
                    phases[target] = required
                    changed = True
        if not changed:
            return phases
        if iteration == len(specs) - 1:
            return None
    return phases


def _infer_clock_domain_registers(
    module: CircuitModule,
) -> tuple[tuple[StateRegister, ...], ...]:
    """Union registers connected by ordinary same-index expressions."""

    registers = tuple(module.state_registers)
    by_name = {register.name: register for register in registers}
    parent = {register.name: register.name for register in registers}
    order = {register.name: index for index, register in enumerate(registers)}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(left: StateRegister, right: StateRegister) -> None:
        left_root = find(left.name)
        right_root = find(right.name)
        if left_root == right_root:
            return
        if order[left_root] <= order[right_root]:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for operation in module.state_operations:
        referenced: set[StateRegister] = set()
        if isinstance(operation, (AccumulatorAdd, FreezeSet)):
            referenced.update(_registers_in_value(operation.value))
        if isinstance(operation, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
            referenced.update(_registers_in_value(operation.when))
        for source in referenced:
            union(operation.register, source)

    for output in module.output.values:
        referenced = sorted(_registers_in_value(output), key=lambda item: order[item.name])
        if referenced:
            first = referenced[0]
            for other in referenced[1:]:
                union(first, other)

    groups: dict[str, list[StateRegister]] = {}
    for register in registers:
        groups.setdefault(find(register.name), []).append(by_name[register.name])
    return tuple(tuple(group) for group in groups.values())


def _registers_in_value(value: object) -> set[StateRegister]:
    from factorio_circuit.frontend import _VectorBinaryOp, _VectorFilter, _VectorScalarOp

    seen: set[int] = set()

    def visit(item: object) -> set[StateRegister]:
        if id(item) in seen:
            return set()
        seen.add(id(item))
        if isinstance(item, VectorRegisterRead):
            return {item.register}
        if isinstance(item, (Input, InputSample, Constant, VectorInput, VectorInputSample, VectorConstant)):
            return set()
        if isinstance(item, VectorSignal):
            return visit(item.vector)
        if isinstance(item, (BinaryOp, Compare)):
            return visit(item.left) | visit(item.right)
        if isinstance(item, Select):
            return visit(item.condition) | visit(item.when_true) | visit(item.when_false)
        if isinstance(item, _VectorBinaryOp):
            return visit(item.left) | visit(item.right)
        if isinstance(item, _VectorScalarOp):
            return visit(item.vector) | visit(item.scalar)
        if isinstance(item, _VectorFilter):
            return visit(item.vector)
        raise TypeError(item)

    return visit(value)


def _collect_state_reads(module: CircuitModule) -> tuple[VectorRegisterRead, ...]:
    from factorio_circuit.frontend import _VectorBinaryOp, _VectorFilter, _VectorScalarOp

    result: list[VectorRegisterRead] = []
    seen_reads: set[int] = set()
    traversed: set[int] = set()

    def add(value: object) -> None:
        if id(value) in traversed:
            return
        traversed.add(id(value))
        if isinstance(value, VectorRegisterRead):
            if id(value) not in seen_reads:
                seen_reads.add(id(value))
                result.append(value)
            return
        if isinstance(value, (Input, InputSample, Constant, VectorInput, VectorInputSample, VectorConstant)):
            return
        if isinstance(value, VectorSignal):
            add(value.vector)
            return
        if isinstance(value, (BinaryOp, Compare)):
            add(value.left)
            add(value.right)
            return
        if isinstance(value, Select):
            add(value.condition)
            add(value.when_true)
            add(value.when_false)
            return
        if isinstance(value, _VectorBinaryOp):
            add(value.left)
            add(value.right)
            return
        if isinstance(value, _VectorScalarOp):
            add(value.vector)
            add(value.scalar)
            return
        if isinstance(value, _VectorFilter):
            add(value.vector)
            return
        raise TypeError(value)

    for output in module.output.values:
        add(output)
    for operation in module.state_operations:
        if isinstance(operation, (AccumulatorAdd, FreezeSet)):
            add(operation.value)
        if isinstance(operation, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
            add(operation.when)
    return tuple(result)
