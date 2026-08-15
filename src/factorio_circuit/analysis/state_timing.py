"""Logical state timing and physical clock-period inference.

Logical steps and Factorio game ticks are separate coordinates.  Stateless combinators preserve a
logical step while adding physical latency.  Register transitions advance logical state and may need
more than one physical tick per logical step.  The analyzer groups ordinary state dependencies into
clock domains and chooses the smallest feasible physical period for each domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from factorio_circuit.events import EventCausalityError, EventThroughputError
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    ClockContractEnvironment,
    ClockId,
    Compare,
    Constant,
    EventScalarFlow,
    EventVectorFlow,
    Input,
    InputSample,
    SampleOn,
    ScalarValue,
    Select,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    VectorValue,
    has_event_usage,
    reject_event_module,
    validate_canonical_module,
)
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    AccumulatorRegister,
    FreezeRegister,
    FreezeSet,
    StateOperation,
    StateRegister,
    StateTransition,
    VectorRegisterRead,
    state_transitions,
)

from .causality import CausalityEdge, CausalityEdgeKind, CausalityGraph, has_nonpositive_cycle
from .latency import FACTORIO_LATENCY


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
    clock_id: ClockId | None = None


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
    event_clocks: tuple[EventClockTiming, ...] = ()
    clock_environment: ClockContractEnvironment = ClockContractEnvironment()
    unsupported_crossings: tuple[UnsupportedClockCrossing, ...] = ()

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
            return None if self.event_clocks else 1
        if len(periods) == 1:
            return next(iter(periods))
        return None


@dataclass(frozen=True, slots=True)
class EventClockTiming:
    """Derived timing for one independent irregular external-Event clock."""

    clock_id: ClockId
    required_min_separation: int
    guaranteed_min_separation: int
    legacy_required_min_separation: int | None = None

    def __post_init__(self) -> None:
        if self.required_min_separation < 1 or self.guaranteed_min_separation < 1:
            raise ValueError("Event clock separations must be positive")
        if self.legacy_required_min_separation is not None and (
            self.legacy_required_min_separation < 1
        ):
            raise ValueError("legacy Event separations must be positive")

    @property
    def feasible(self) -> bool:
        return self.guaranteed_min_separation >= self.required_min_separation


@dataclass(frozen=True, slots=True)
class UnsupportedClockCrossing:
    """Semantic dependency retained for a later physical rate-crossing diagnostic."""

    source: StateRegister
    target: StateRegister
    source_clock: ClockId
    target_clock: ClockId


@dataclass(frozen=True, slots=True)
class _Requirement:
    """Availability of one leaf after a logical displacement and physical latency."""

    source: StateRegister | None
    logical_offset: int
    latency: int


@dataclass(frozen=True, slots=True)
class _RegisterSpec:
    register: StateRegister
    operations: tuple[StateOperation | StateTransition, ...]
    reads: tuple[VectorRegisterRead, ...]
    commit_offset: int
    first_update_order: int
    last_update_order: int
    requirements: tuple[_Requirement, ...]


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


def _operation_value(operation: StateOperation | StateTransition) -> VectorValue | None:
    if isinstance(operation, StateTransition):
        return operation.value
    if isinstance(operation, (AccumulatorAdd, FreezeSet)):
        return operation.value
    return None


def _operation_when(operation: StateOperation | StateTransition) -> ScalarValue | None:
    return operation.when


def _analyze_event_timing(
    module: CircuitModule,
    transitions: tuple[StateTransition, ...],
    environment: ClockContractEnvironment,
) -> StateTimingPlan:
    """Derive independent Event separations from state recurrence requirements."""

    declared_events = set(module.event_inputs)
    register_clocks: dict[StateRegister, set[ClockId]] = {}
    event_transitions = tuple(
        transition for transition in transitions if transition.trigger is not None
    )
    for transition in transitions:
        register_clocks.setdefault(transition.register, set()).add(transition.clock.clock_id)
        if transition.trigger is not None and transition.trigger not in declared_events:
            raise EventCausalityError("Event transition trigger is not declared by the module")

    edges: list[CausalityEdge] = []
    requirements_by_clock: dict[ClockId, int] = {}
    legacy_requirements_by_clock: dict[ClockId, int] = {}
    crossings: list[UnsupportedClockCrossing] = []
    for transition in event_transitions:
        trigger = transition.trigger
        assert trigger is not None  # guarded above
        clock_id = trigger.clock.clock_id
        requirements: list[_Requirement] = []
        if transition.value is not None:
            requirements.extend(_vector_requirements(transition.value))
        if transition.when is not None:
            requirements.extend(_control_requirements(transition.when))
        required = 1
        if transition.required_min_separation is not None:
            legacy_requirements_by_clock[clock_id] = max(
                legacy_requirements_by_clock.get(clock_id, 1),
                transition.required_min_separation,
            )
        for requirement in requirements:
            if requirement.source is None:
                continue
            source_clock_set = register_clocks.get(requirement.source)
            if not source_clock_set:
                raise EventCausalityError(
                    f"Event transition reads state {requirement.source.name!r} without an Event "
                    "update"
                )
            if len(source_clock_set) != 1 or clock_id not in source_clock_set:
                source_clock = next(iter(source_clock_set))
                crossings.append(
                    UnsupportedClockCrossing(
                        source=requirement.source,
                        target=transition.register,
                        source_clock=source_clock,
                        target_clock=clock_id,
                    )
                )
            displacement = transition.logical_offset + 1 - requirement.logical_offset
            edge = CausalityEdge(
                source=requirement.source,
                target=transition.register,
                kind=CausalityEdgeKind.EVENT_STATE_DEPENDENCY,
                logical_displacement=displacement,
                physical_latency=FACTORIO_LATENCY.state_edge_latency(requirement.latency),
            )
            edges.append(edge)
            if clock_id in source_clock_set and displacement > 0:
                required = max(required, ceil(edge.physical_latency / displacement))
        requirements_by_clock[clock_id] = max(requirements_by_clock.get(clock_id, 1), required)

    # Causality is a logical property and is intentionally checked before throughput.
    graph = CausalityGraph(
        registers=module.state_registers,
        edges=tuple(edges),
    )
    if has_nonpositive_cycle(graph):
        names = ", ".join(register.name for register in module.state_registers)
        raise EventCausalityError(
            "Event state recurrence has no causal ordering: nonpositive logical cycle "
            f"in domain {{{names}}}"
        )

    if not event_transitions:
        for source in module.event_inputs:
            requirements_by_clock.setdefault(source.clock.clock_id, 1)
    ordered_clock_ids = tuple(
        dict.fromkeys(
            [source.clock.clock_id for source in module.event_inputs] + list(requirements_by_clock)
        )
    )
    event_timings = tuple(
        EventClockTiming(
            clock_id=clock_id,
            required_min_separation=requirements_by_clock[clock_id],
            guaranteed_min_separation=environment.contract_for(clock_id).guaranteed_min_separation,
            legacy_required_min_separation=legacy_requirements_by_clock.get(clock_id),
        )
        for clock_id in ordered_clock_ids
        if clock_id in requirements_by_clock
    )
    return StateTimingPlan((), (), event_timings, environment, tuple(crossings))


def analyze_normalized_state_timing(
    module: CircuitModule,
    *,
    allow_event_declarations: bool = False,
) -> StateTimingPlan:
    """Infer logical clock domains, their minimal periods, and concrete physical phases.

    For a register with state phase ``phi`` and domain period ``P``, logical state ``S[k]`` is
    observable at physical tick ``phi + k*P``.  A transition committed between logical boundaries
    ``k`` and ``k+1`` receives its physical update input one game tick before the latter boundary.

    Ordinary expressions preserve logical indices.  Therefore any ordinary expression connecting
    state registers places those registers in the same clock domain.  Different domains may still
    share raw external inputs; explicit cross-domain state resampling is intentionally not present
    yet.
    """

    if not allow_event_declarations:
        reject_event_module(module)
        validate_canonical_module(module)
    if not module.state_registers:
        return StateTimingPlan((), ())

    periodic_operations = tuple(
        transition for transition in state_transitions(module) if transition.trigger is None
    )
    active_registers = tuple(
        register
        for register in module.state_registers
        if any(operation.register == register for operation in periodic_operations)
    )
    if not active_registers:
        return StateTimingPlan((), ())
    reads = _collect_state_reads(module, periodic_operations)
    specs: list[_RegisterSpec] = []
    for register in active_registers:
        operations = tuple(op for op in periodic_operations if op.register == register)
        register_reads = tuple(read for read in reads if read.register == register)
        specs.append(_analyze_register_semantics(register, operations, register_reads))

    specs_by_name = {spec.register.name: spec for spec in specs}
    groups = _infer_clock_domain_registers(module, periodic_operations, active_registers)
    register_clocks = dict(module.register_clocks)
    for fallback_transition in state_transitions(module):
        if fallback_transition.trigger is None:
            register_clocks.setdefault(fallback_transition.register, fallback_transition.clock)
    clocks_seen: dict[ClockId, tuple[StateRegister, ...]] = {}
    for group in groups:
        clocks = {register_clocks[register] for register in group}
        if len(clocks) != 1:
            raise StateTimingError(
                "canonical register clock mapping disagrees with inferred state domain"
            )
        clock = next(iter(clocks))
        clock_id = clock.clock_id
        if clock_id in clocks_seen:
            raise StateTimingError(
                "canonical register clock mapping splits one structural clock domain"
            )
        clocks_seen[clock_id] = group
    causality = _causality_graph(tuple(specs))
    for registers in groups:
        register_set = set(registers)
        domain_graph = CausalityGraph(
            registers=registers,
            edges=tuple(
                edge
                for edge in causality.edges
                if edge.source in register_set and edge.target in register_set
            ),
        )
        if has_nonpositive_cycle(domain_graph):
            names = ", ".join(register.name for register in registers)
            raise StateTimingError(
                "state recurrence has no finite logical clock period: ordinary same-step "
                f"dependencies form a noncausal/zero-distance physical cycle in domain {{{names}}}"
            )

    domain_timings: list[ClockDomainTiming] = []
    phase_by_name: dict[str, int] = {}
    period_by_name: dict[str, int] = {}
    domain_by_name: dict[str, int] = {}

    for domain_id, registers in enumerate(groups):
        domain_specs = [specs_by_name[register.name] for register in registers]
        period, phases = _solve_domain(domain_specs)
        domain_timings.append(
            ClockDomainTiming(
                domain_id,
                period,
                registers,
                register_clocks[registers[0]].clock_id,
            )
        )
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


def analyze_state_timing(module: CircuitModule) -> StateTimingPlan:
    """Compatibility wrapper that normalizes a public legacy module once."""

    from factorio_circuit.lowering.frontend_to_ir import normalize_module

    return analyze_normalized_state_timing(normalize_module(module))


def analyze_clocked_timing(
    module: CircuitModule,
    *,
    clock_environment: ClockContractEnvironment | None = None,
) -> StateTimingPlan:
    """Analyze either periodic Level state or irregular external-Event state transitions.

    Event analysis is semantic-only: it derives required source separation and causality edges but
    never invents a periodic clock or a physical Event pulse.  Logical causality is checked before
    any throughput comparison so a malformed recurrence cannot be hidden by a generous contract.
    """

    from factorio_circuit.lowering.frontend_to_ir import normalize_module

    normalized = normalize_module(module)
    environment = (
        clock_environment
        if clock_environment is not None
        else ClockContractEnvironment.from_module(normalized)
    )
    transitions = state_transitions(normalized)
    has_event_transitions = any(transition.trigger is not None for transition in transitions)
    has_periodic_transitions = any(transition.trigger is None for transition in transitions)
    if not has_event_transitions and not has_event_usage(normalized):
        plan = analyze_normalized_state_timing(
            normalized,
            allow_event_declarations=bool(normalized.event_inputs),
        )
        return StateTimingPlan(
            plan.domains,
            plan.registers,
            plan.event_clocks,
            environment,
            plan.unsupported_crossings,
        )

    event_plan = _analyze_event_timing(normalized, transitions, environment)
    if not has_periodic_transitions:
        return event_plan
    periodic_plan = analyze_normalized_state_timing(
        normalized,
        allow_event_declarations=True,
    )
    return StateTimingPlan(
        periodic_plan.domains,
        periodic_plan.registers,
        event_plan.event_clocks,
        environment,
        event_plan.unsupported_crossings,
    )


def validate_event_throughput(
    plan: StateTimingPlan,
    *,
    clock_environment: ClockContractEnvironment | None = None,
) -> None:
    """Raise when any derived Event requirement exceeds its authoritative guarantee."""

    environment = clock_environment if clock_environment is not None else plan.clock_environment
    for timing in plan.event_clocks:
        guaranteed = environment.contract_for(timing.clock_id).guaranteed_min_separation
        if guaranteed < timing.required_min_separation:
            raise EventThroughputError(
                f"Event clock {timing.clock_id.identity!r} guarantee {guaranteed} "
                f"is below derived minimum separation {timing.required_min_separation}"
            )


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
    if isinstance(value, EventScalarFlow):
        return (_Requirement(None, 0, 0),)
    if isinstance(value, SampleOn):
        return _scalar_requirements(value.source)  # type: ignore[arg-type]
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
            (*_scalar_requirements(value.left), *_scalar_requirements(value.right)),
            FACTORIO_LATENCY.operation_latency("scalar_binary", value.op),
        )
    if isinstance(value, Select):
        # The conservative generic mux is false + (true-false)*condition.  Keep the same timing
        # envelope as the previous analyzer even when physical optimization later fuses the mux.
        return (
            *_delay_requirements(
                _scalar_requirements(value.when_true),
                FACTORIO_LATENCY.operation_latency("select_data", value.name),
            ),
            *_delay_requirements(
                _scalar_requirements(value.when_false),
                FACTORIO_LATENCY.operation_latency("select_data", value.name),
            ),
            *_delay_requirements(
                _scalar_requirements(value.condition),
                FACTORIO_LATENCY.operation_latency("select_condition", value.name),
            ),
        )
    raise TypeError(value)


def _control_requirements(value: ScalarValue) -> tuple[_Requirement, ...]:
    """Requirements after nonzero/zero normalization at a state boundary."""

    if isinstance(value, Constant):
        return ()
    return _delay_requirements(
        _scalar_requirements(value), FACTORIO_LATENCY.operation_latency("scalar_binary", "control")
    )


def _vector_requirements(value: object) -> tuple[_Requirement, ...]:
    if isinstance(value, EventVectorFlow):
        return (_Requirement(None, 0, 0),)
    if isinstance(value, SampleOn):
        return _vector_requirements(value.source)
    if isinstance(value, VectorInput):
        return (_Requirement(None, 0, 0),)
    if isinstance(value, VectorInputSample):
        return (_Requirement(None, value.offset, 0),)
    if isinstance(value, VectorConstant):
        return (_Requirement(None, 0, 0),)
    if isinstance(value, VectorRegisterRead):
        return (_Requirement(value.register, value.offset, 0),)
    if isinstance(value, VectorBinaryOp):
        return _delay_requirements(
            (*_vector_requirements(value.left), *_vector_requirements(value.right)),
            FACTORIO_LATENCY.operation_latency("vector_binary", value.op),
        )
    if isinstance(value, VectorScalarOp):
        return _delay_requirements(
            (*_vector_requirements(value.vector), *_scalar_requirements(value.scalar)),
            FACTORIO_LATENCY.operation_latency("vector_scalar", value.op),
        )
    if isinstance(value, (VectorFilter, VectorSelect)):
        return _delay_requirements(
            _vector_requirements(value.vector),
            FACTORIO_LATENCY.operation_latency(
                "vector_select" if isinstance(value, VectorSelect) else "vector_filter",
                value.op,
            ),
        )
    raise TypeError(value)


def _analyze_register_semantics(
    register: StateRegister,
    operations: tuple[StateOperation | StateTransition, ...],
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
        adds = [op for op in operations if _operation_kind(op) == "add"]
        clears = [op for op in operations if _operation_kind(op) == "clear"]
        unexpected = [op for op in operations if _operation_kind(op) not in {"add", "clear"}]
        if unexpected:  # pragma: no cover
            raise StateTimingError(f"unexpected operation for AccumulatorReg {register.name!r}")
        if not adds:
            raise StateTimingError(
                f"AccumulatorReg {register.name!r} requires at least one .add(...)"
            )
        if len(clears) > 1:
            raise StateTimingError(f"AccumulatorReg {register.name!r} has multiple clear controls")

        clear_when = _operation_when(clears[0]) if clears else None
        clear_requirements = _control_requirements(clear_when) if clear_when is not None else ()
        for add in adds:
            add_value = _operation_value(add)
            add_when = _operation_when(add)
            if add_value is None or add_when is None:
                raise StateTimingError("add transition is missing its value or condition")
            requirements.extend(_vector_requirements(add_value))
            requirements.extend(_control_requirements(add_when))
            # A physical state gate can test add-enable and clear-disable in one decider stage.
            requirements.extend(clear_requirements)
        requirements.extend(clear_requirements)

    elif isinstance(register, FreezeRegister):
        freeze_sets = [op for op in operations if _operation_kind(op) == "set"]
        freeze_unexpected = [op for op in operations if _operation_kind(op) != "set"]
        if freeze_unexpected:  # pragma: no cover
            raise StateTimingError(f"unexpected operation for FreezeReg {register.name!r}")
        if len(freeze_sets) != 1:
            raise StateTimingError(
                f"FreezeReg {register.name!r} requires exactly one .set(data, when=...) call"
            )
        set_value = _operation_value(freeze_sets[0])
        set_when = _operation_when(freeze_sets[0])
        if set_value is None or set_when is None:
            raise StateTimingError("set transition is missing its value or condition")
        requirements.extend(_vector_requirements(set_value))
        requirements.extend(_control_requirements(set_when))

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


def _causality_graph(specs: tuple[_RegisterSpec, ...]) -> CausalityGraph:
    """Project state-bearing timing requirements into the internal causality graph."""

    edges: list[CausalityEdge] = []
    for spec in specs:
        for requirement in spec.requirements:
            if requirement.source is None:
                continue
            edges.append(
                CausalityEdge(
                    source=requirement.source,
                    target=spec.register,
                    kind=CausalityEdgeKind.ORDINARY_STATE_DEPENDENCY,
                    logical_displacement=spec.commit_offset + 1 - requirement.logical_offset,
                    physical_latency=FACTORIO_LATENCY.state_edge_latency(requirement.latency),
                )
            )
    return CausalityGraph(
        registers=tuple(spec.register for spec in specs),
        edges=tuple(edges),
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
        FACTORIO_LATENCY.state_edge_latency(requirement.latency)
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


def _solve_phases_for_period(specs: list[_RegisterSpec], period: int) -> dict[str, int] | None:
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
                + FACTORIO_LATENCY.state_transition_latency("commit"),
            )
        phases[spec.register.name] = lower_bound

    # phi_target >= phi_source + (r-c-1)P + latency + state_commit_stage.
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
                        f"state {target!r} depends on state {source.name!r} "
                        "in another clock domain; "
                        "ordinary state expressions must share one logical clock domain"
                    )
                required = (
                    phases[source.name]
                    + (requirement.logical_offset - spec.commit_offset - 1) * period
                    + requirement.latency
                    + FACTORIO_LATENCY.state_transition_latency("commit")
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
    operations: tuple[StateOperation | StateTransition, ...] | None = None,
    registers: tuple[StateRegister, ...] | None = None,
) -> tuple[tuple[StateRegister, ...], ...]:
    """Union registers connected by ordinary same-index expressions."""

    active_registers = tuple(module.state_registers if registers is None else registers)
    by_name = {register.name: register for register in active_registers}
    parent = {register.name: register.name for register in active_registers}
    order = {register.name: index for index, register in enumerate(active_registers)}

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

    for operation in operations if operations is not None else module.state_operations:
        referenced: set[StateRegister] = set()
        value = _operation_value(operation)
        when = _operation_when(operation)
        if _operation_kind(operation) in {"add", "set"} and value is not None:
            referenced.update(_registers_in_value(value))
        if _operation_kind(operation) in {"add", "clear", "set"} and when is not None:
            referenced.update(_registers_in_value(when))
        if operation.register.name in by_name:
            for source in referenced:
                if source.name in by_name:
                    union(operation.register, source)

    for output in module.output.values:
        output_referenced = sorted(
            (item for item in _registers_in_value(output) if item.name in by_name),
            key=lambda item: order[item.name],
        )
        if output_referenced:
            first = output_referenced[0]
            for other in output_referenced[1:]:
                union(first, other)

    groups: dict[str, list[StateRegister]] = {}
    for register in active_registers:
        groups.setdefault(find(register.name), []).append(by_name[register.name])
    return tuple(tuple(group) for group in groups.values())


def _registers_in_value(value: object) -> set[StateRegister]:
    seen: set[int] = set()

    def visit(item: object) -> set[StateRegister]:
        if id(item) in seen:
            return set()
        seen.add(id(item))
        if isinstance(item, VectorRegisterRead):
            return {item.register}
        if isinstance(
            item, (Input, InputSample, Constant, VectorInput, VectorInputSample, VectorConstant)
        ):
            return set()
        if isinstance(item, VectorSignal):
            return visit(item.vector)
        if isinstance(item, (BinaryOp, Compare)):
            return visit(item.left) | visit(item.right)
        if isinstance(item, Select):
            return visit(item.condition) | visit(item.when_true) | visit(item.when_false)
        if isinstance(item, VectorBinaryOp):
            return visit(item.left) | visit(item.right)
        if isinstance(item, VectorScalarOp):
            return visit(item.vector) | visit(item.scalar)
        if isinstance(item, (VectorFilter, VectorSelect)):
            return visit(item.vector)
        raise TypeError(item)

    return visit(value)


def _collect_state_reads(
    module: CircuitModule,
    operations: tuple[StateOperation | StateTransition, ...] | None = None,
) -> tuple[VectorRegisterRead, ...]:
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
        if isinstance(
            value, (Input, InputSample, Constant, VectorInput, VectorInputSample, VectorConstant)
        ):
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
        if isinstance(value, VectorBinaryOp):
            add(value.left)
            add(value.right)
            return
        if isinstance(value, VectorScalarOp):
            add(value.vector)
            add(value.scalar)
            return
        if isinstance(value, (VectorFilter, VectorSelect)):
            add(value.vector)
            return
        raise TypeError(value)

    for output in module.output.values:
        add(output)
    for operation in operations if operations is not None else module.state_operations:
        value = _operation_value(operation)
        when = _operation_when(operation)
        if _operation_kind(operation) in {"add", "set"} and value is not None:
            add(value)
        if _operation_kind(operation) in {"add", "clear", "set"} and when is not None:
            add(when)
    return tuple(result)
