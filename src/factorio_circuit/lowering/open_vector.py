"""Whole-vector lowering plus periodic realization of logical clock domains."""

from __future__ import annotations

from factorio_circuit.analysis.state_timing import StateTimingError, StateTimingPlan
from factorio_circuit.frontend import (
    _VectorBinaryOp,
    _VectorFilter,
    _VectorScalarOp,
    _VectorSelect,
)
from factorio_circuit.ir.abstract_physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    DeciderCondition,
    Endpoint,
    Operand,
)
from factorio_circuit.ir.semantic import (
    CircuitModule,
    Constant,
    InputSample,
    Value,
    VectorInputSample,
    VectorValue,
)
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    AccumulatorRegister,
    FreezeRegister,
    FreezeSet,
    StateRegister,
)
from factorio_circuit.lowering.ir_to_abstract_physical import AbstractPhysicalLowerer as _Base
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedValue, RealizedVector

from .vector_binary import realize_vector_binary
from .vector_select import realize_vector_select
from .vector_unary import realize_vector_filter, realize_vector_scalar

type _ConditionBranch = tuple[
    DeciderCondition,
    tuple[DeciderCondition, ...],
    tuple[int, ...],
    str,
]


class VectorLowerer(_Base):
    """Lower runtime-open vectors and logical clock domains onto Factorio combinators."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        enable_packing: bool,
        state_timing: StateTimingPlan | None = None,
    ) -> None:
        super().__init__(module, enable_packing=enable_packing, state_timing=state_timing)
        self._clock_counters: dict[int, tuple[int, int, int]] = {}
        self._startup_source: RealizedValue | None = None
        self._external_sample_period = self.state_timing.uniform_period

    def realize(self, value: Value) -> RealizedValue:
        if isinstance(value, InputSample):
            cached = self.memo.get(id(value))
            if cached is not None:
                return cached
            base = self.memo.get(id(value.source))
            if base is None:
                raise ValueError(f"input {value.source.name!r} was not initialized")
            period = self._sample_period(value.offset)
            result = RealizedValue(
                base.signal,
                base.net,
                value.offset * period,
                base.clean_single_lane,
            )
            self.memo[id(value)] = result
            return result
        return super().realize(value)

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        item: object = value
        cached = self.vector_memo.get(id(item))
        if cached is not None:
            return cached
        if isinstance(item, VectorInputSample):
            base = self.vector_memo.get(id(item.source))
            if base is None:
                raise ValueError(f"vector input {item.source.name!r} was not initialized")
            period = self._sample_period(item.offset)
            result = RealizedVector(base.net, item.offset * period)
        elif isinstance(item, _VectorBinaryOp):
            result = realize_vector_binary(self, item)
        elif isinstance(item, _VectorScalarOp):
            result = realize_vector_scalar(self, item)
        elif isinstance(item, _VectorSelect):
            result = realize_vector_select(self, item)
        elif isinstance(item, _VectorFilter):
            result = realize_vector_filter(self, item)
        else:
            return super().realize_vector(value)
        self.vector_memo[id(item)] = result
        return result

    def _sample_period(self, offset: int) -> int:
        if offset == 0:
            return 1
        if self._external_sample_period is None:
            raise StateTimingError(
                "fresh external samples are shared across clock domains with different periods; "
                "explicit domain-bound sampling/resampling is not implemented yet"
            )
        return self._external_sample_period

    def _clock_counter(self, register: StateRegister) -> tuple[int, int, int]:
        timing = self.state_timing.for_register(register)
        domain_id = timing.clock_domain
        cached = self._clock_counters.get(domain_id)
        if cached is not None:
            return cached
        period = timing.period
        if period <= 1:  # pragma: no cover - callers only request multicycle clocks
            raise ValueError("periodic clock requested for a one-tick domain")

        signal = self._new_signal(f"clock domain {domain_id}")
        source = ConstantCombinator(
            id=self._take_entity_id(),
            signals=((signal, 1),),
            description=f"clock domain {domain_id}: +1",
        )
        counter_id = self._take_entity_id()
        net = self._new_net(
            (signal,),
            Endpoint(source.id, Connector.SINGLE),
            label=f"clock domain {domain_id}: modulo-{period}",
        )
        counter = ArithmeticCombinator(
            id=counter_id,
            operation="%",
            left=Operand(signal=signal, nets=(net,)),
            right=Operand(constant=period),
            output_each=False,
            output_signal=signal,
            description=f"clock domain {domain_id}: modulo-{period} counter",
        )
        self.circuit.entities.extend((source, counter))
        self._attach(net, Endpoint(counter_id, Connector.INPUT))
        self._attach(net, Endpoint(counter_id, Connector.OUTPUT))
        result = (signal, net, period)
        self._clock_counters[domain_id] = result
        return result

    def _clock_condition(
        self, register: StateRegister, target_phase: int, *, equal: bool
    ) -> tuple[DeciderCondition, int]:
        signal, net, period = self._clock_counter(register)
        input_phase = target_phase - 1
        if input_phase < 0:  # pragma: no cover - a multicycle transition is at least phase 1
            raise ValueError("multicycle state gate has no preceding physical tick")
        # The feedback net carries constant +1 plus the counter output, so after warm-up the wire
        # cycles through 1..period. State gates inspect the tick immediately before their output.
        value = input_phase % period + 1
        return (
            DeciderCondition(
                comparator="==" if equal else "!=",
                left=Operand(signal=signal, nets=(net,)),
                right=Operand(constant=value),
                compare_type="and",
            ),
            net,
        )

    def _startup_ready(self, target_phase: int) -> RealizedValue:
        """Return a level that becomes one at the first valid state-gate input tick.

        A modulo clock has the right steady-state cadence but repeats its residue before a long
        acyclic pipeline has produced logical step zero.  Delaying a constant one to the state
        gate's input phase suppresses those premature residues without changing steady-state P.
        """

        input_phase = target_phase - 1
        if input_phase < 0:  # pragma: no cover - multicycle transitions have a preceding tick
            raise ValueError("multicycle state startup has no preceding physical tick")
        if self._startup_source is None:
            signal = self._new_signal("logical state startup")
            entity = ConstantCombinator(
                id=self._take_entity_id(),
                signals=((signal, 1),),
                description="logical state startup: constant one",
            )
            self.circuit.entities.append(entity)
            net = self._new_net(
                (signal,),
                Endpoint(entity.id, Connector.SINGLE),
                label="logical state startup",
            )
            self._startup_source = RealizedValue(signal, net, 0)
        return self.delay_to(self._startup_source, input_phase)

    @staticmethod
    def _boolean_condition(value: RealizedValue, *, nonzero: bool) -> DeciderCondition:
        return DeciderCondition(
            comparator="!=" if nonzero else "==",
            left=Operand(signal=value.signal, nets=(value.net,)),
            right=Operand(constant=0),
            compare_type="and",
        )

    def _emit_condition_union(
        self,
        *,
        branches: tuple[_ConditionBranch, ...],
        target_phase: int,
        label: str,
    ) -> RealizedValue:
        """OR mutually exclusive conditions by letting their deciders drive one signal/net."""

        if not branches:
            raise ValueError("condition union requires at least one branch")
        signal = self._new_signal(label)
        output_net: int | None = None
        for primary, additional, input_nets, description in branches:
            entity = DeciderCombinator(
                id=self._take_entity_id(),
                comparator=primary.comparator,
                left=primary.left,
                right=primary.right,
                output_signal=signal,
                output_constant=1,
                additional_conditions=additional,
                description=description,
            )
            self.circuit.entities.append(entity)
            input_endpoint = Endpoint(entity.id, Connector.INPUT)
            for net in dict.fromkeys(input_nets):
                self._attach(net, input_endpoint)
            output_endpoint = Endpoint(entity.id, Connector.OUTPUT)
            if output_net is None:
                output_net = self._new_net((signal,), output_endpoint, label=label)
            else:
                self._attach(output_net, output_endpoint)
        assert output_net is not None
        return RealizedValue(signal, output_net, target_phase)

    def _emit_condition_signal(
        self,
        *,
        primary: DeciderCondition,
        additional: tuple[DeciderCondition, ...],
        input_nets: tuple[int, ...],
        target_phase: int,
        label: str,
    ) -> RealizedValue:
        return self._emit_condition_union(
            branches=((primary, additional, input_nets, label),),
            target_phase=target_phase,
            label=label,
        )

    def _lower_freeze(self, register: FreezeRegister) -> None:
        sets = [
            op
            for op in self.module.state_operations
            if isinstance(op, FreezeSet) and op.register == register
        ]
        if len(sets) != 1:
            raise ValueError(
                f"FreezeReg {register.name!r} requires exactly one .set(data, when=...) call"
            )
        spec = sets[0]
        timing = self.state_timing.for_register(register)
        target_phase = timing.transition_input_phase
        source = self.delay_vector_to(self.realize_vector(spec.value), target_phase)

        constant_when = spec.when if isinstance(spec.when, Constant) else None
        constant_active = constant_when is not None and constant_when.value != 0
        constant_inactive = constant_when is not None and constant_when.value == 0

        clock_equal: DeciderCondition | None = None
        clock_not_equal: DeciderCondition | None = None
        clock_net: int | None = None
        ready: RealizedValue | None = None
        if timing.period > 1:
            clock_equal, clock_net = self._clock_condition(register, target_phase, equal=True)
            clock_not_equal, _ = self._clock_condition(register, target_phase, equal=False)
            ready = self._startup_ready(target_phase)

        control: RealizedValue | None = None
        if constant_when is None:
            control = self.delay_to(self.realize(spec.when), target_phase - 1)

        pass_value: RealizedValue | None = None
        if not constant_inactive and not (constant_active and timing.period == 1):
            conditions: list[DeciderCondition] = []
            input_nets: list[int] = []
            if control is not None:
                conditions.append(self._boolean_condition(control, nonzero=True))
                input_nets.append(control.net)
            if ready is not None:
                conditions.append(self._boolean_condition(ready, nonzero=True))
                input_nets.append(ready.net)
            if clock_equal is not None:
                conditions.append(clock_equal)
                assert clock_net is not None
                input_nets.append(clock_net)
            assert conditions
            pass_value = self._emit_condition_signal(
                primary=conditions[0],
                additional=tuple(conditions[1:]),
                input_nets=tuple(input_nets),
                target_phase=target_phase,
                label=f"FreezeReg {register.name}: pass",
            )

        hold_value: RealizedValue | None = None
        if constant_inactive:
            hold_value = None
        elif timing.period == 1:
            if control is not None:
                hold_value = self._emit_condition_signal(
                    primary=self._boolean_condition(control, nonzero=False),
                    additional=(),
                    input_nets=(control.net,),
                    target_phase=target_phase,
                    label=f"FreezeReg {register.name}: hold",
                )
        else:
            assert ready is not None
            assert clock_equal is not None and clock_not_equal is not None and clock_net is not None
            ready_true = self._boolean_condition(ready, nonzero=True)
            branches: list[_ConditionBranch] = [
                (
                    self._boolean_condition(ready, nonzero=False),
                    (),
                    (ready.net,),
                    f"FreezeReg {register.name}: hold before startup",
                ),
                (
                    ready_true,
                    (clock_not_equal,),
                    (ready.net, clock_net),
                    f"FreezeReg {register.name}: hold between logical boundaries",
                ),
            ]
            if control is not None:
                branches.append(
                    (
                        self._boolean_condition(control, nonzero=False),
                        (ready_true, clock_equal),
                        (control.net, ready.net, clock_net),
                        f"FreezeReg {register.name}: hold at inactive logical boundary",
                    )
                )
            hold_value = self._emit_condition_union(
                branches=tuple(branches),
                target_phase=target_phase,
                label=f"FreezeReg {register.name}: hold",
            )

        memory_id = self.state_memory_ids[register.name]
        memory_net = self.state_memory_nets[register.name]

        if not constant_inactive:
            if pass_value is None:
                gate = ArithmeticCombinator(
                    id=self._take_entity_id(),
                    operation="+",
                    left=Operand(each=True, nets=(source.net,)),
                    right=Operand(constant=0),
                    output_each=True,
                    description=f"FreezeReg {register.name}: unconditional input gate",
                )
                self.circuit.entities.append(gate)
                self._attach(source.net, Endpoint(gate.id, Connector.INPUT))
            else:
                self._add_net_conflict(
                    source.net,
                    pass_value.net,
                    f"FreezeReg {register.name}: transparent data/control isolation",
                )
                gate = ArithmeticCombinator(
                    id=self._take_entity_id(),
                    operation="*",
                    left=Operand(each=True, nets=(source.net,)),
                    right=Operand(signal=pass_value.signal, nets=(pass_value.net,)),
                    output_each=True,
                    description=f"FreezeReg {register.name}: input gate",
                )
                self.circuit.entities.append(gate)
                gate_input = Endpoint(gate.id, Connector.INPUT)
                self._attach(source.net, gate_input)
                self._attach(pass_value.net, gate_input)
            self._attach(memory_net, Endpoint(gate.id, Connector.OUTPUT))

        if constant_inactive:
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="+",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(constant=0),
                output_each=True,
                description=f"FreezeReg {register.name}: held vector memory",
            )
        elif hold_value is None:
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="*",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(constant=0),
                output_each=True,
                description=f"FreezeReg {register.name}: replaced vector memory",
            )
        else:
            self._add_net_conflict(
                memory_net,
                hold_value.net,
                f"FreezeReg {register.name}: memory data/hold isolation",
            )
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="*",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(signal=hold_value.signal, nets=(hold_value.net,)),
                output_each=True,
                description=f"FreezeReg {register.name}: vector memory",
            )
            self._attach(hold_value.net, Endpoint(memory_id, Connector.INPUT))
        self.circuit.entities.append(memory)

    def _lower_accumulator(self, register: AccumulatorRegister) -> None:
        adds = [
            op
            for op in self.module.state_operations
            if isinstance(op, AccumulatorAdd) and op.register == register
        ]
        clears = [
            op
            for op in self.module.state_operations
            if isinstance(op, AccumulatorClear) and op.register == register
        ]
        if not adds:
            raise ValueError(
                f"AccumulatorReg {register.name!r} requires at least one .add(...) source"
            )
        if len(clears) > 1:
            raise ValueError(f"AccumulatorReg {register.name!r} has multiple clear controls")

        timing = self.state_timing.for_register(register)
        target_phase = timing.transition_input_phase

        clock_equal: DeciderCondition | None = None
        clock_not_equal: DeciderCondition | None = None
        clock_net: int | None = None
        ready: RealizedValue | None = None
        if timing.period > 1:
            clock_equal, clock_net = self._clock_condition(register, target_phase, equal=True)
            clock_not_equal, _ = self._clock_condition(register, target_phase, equal=False)
            ready = self._startup_ready(target_phase)

        clear_value = clears[0].when if clears else None
        clear_constant = clear_value if isinstance(clear_value, Constant) else None
        clear_always = clear_constant is not None and clear_constant.value != 0
        clear_never = clear_value is None or (
            clear_constant is not None and clear_constant.value == 0
        )
        clear_realized: RealizedValue | None = None
        if clear_value is not None and clear_constant is None:
            clear_realized = self.delay_to(self.realize(clear_value), target_phase - 1)

        retain: RealizedValue | None = None
        if clear_never:
            retain = None
        elif timing.period == 1:
            if not clear_always:
                assert clear_realized is not None
                retain = self._emit_condition_signal(
                    primary=self._boolean_condition(clear_realized, nonzero=False),
                    additional=(),
                    input_nets=(clear_realized.net,),
                    target_phase=target_phase,
                    label=f"AccumulatorReg {register.name}: retain",
                )
        else:
            assert ready is not None
            assert clock_equal is not None and clock_not_equal is not None and clock_net is not None
            ready_true = self._boolean_condition(ready, nonzero=True)
            branches: list[_ConditionBranch] = [
                (
                    self._boolean_condition(ready, nonzero=False),
                    (),
                    (ready.net,),
                    f"AccumulatorReg {register.name}: retain before startup",
                ),
                (
                    ready_true,
                    (clock_not_equal,),
                    (ready.net, clock_net),
                    f"AccumulatorReg {register.name}: retain between logical boundaries",
                ),
            ]
            if not clear_always:
                assert clear_realized is not None
                branches.append(
                    (
                        self._boolean_condition(clear_realized, nonzero=False),
                        (ready_true, clock_equal),
                        (clear_realized.net, ready.net, clock_net),
                        f"AccumulatorReg {register.name}: retain at logical boundary",
                    )
                )
            retain = self._emit_condition_union(
                branches=tuple(branches),
                target_phase=target_phase,
                label=f"AccumulatorReg {register.name}: retain",
            )

        for index, add in enumerate(adds):
            if clear_always:
                break
            if isinstance(add.when, Constant) and add.when.value == 0:
                continue

            source = self.delay_vector_to(self.realize_vector(add.value), target_phase)
            active: RealizedValue | None = None

            # In the common P=1, unconditional-add + dynamic-clear case, the retain signal is
            # already exactly clear==0 and can gate the addition without a duplicate decider.
            if (
                timing.period == 1
                and isinstance(add.when, Constant)
                and add.when.value != 0
                and clear_realized is not None
                and retain is not None
            ):
                active = retain
            else:
                conditions: list[DeciderCondition] = []
                input_nets: list[int] = []
                if not isinstance(add.when, Constant):
                    add_control = self.delay_to(self.realize(add.when), target_phase - 1)
                    conditions.append(self._boolean_condition(add_control, nonzero=True))
                    input_nets.append(add_control.net)
                if clear_realized is not None:
                    conditions.append(self._boolean_condition(clear_realized, nonzero=False))
                    input_nets.append(clear_realized.net)
                if ready is not None:
                    conditions.append(self._boolean_condition(ready, nonzero=True))
                    input_nets.append(ready.net)
                if clock_equal is not None:
                    conditions.append(clock_equal)
                    assert clock_net is not None
                    input_nets.append(clock_net)
                if conditions:
                    active = self._emit_condition_signal(
                        primary=conditions[0],
                        additional=tuple(conditions[1:]),
                        input_nets=tuple(input_nets),
                        target_phase=target_phase,
                        label=f"AccumulatorReg {register.name}: add[{index}] active",
                    )

            if active is None:
                gate = ArithmeticCombinator(
                    id=self._take_entity_id(),
                    operation="+",
                    left=Operand(each=True, nets=(source.net,)),
                    right=Operand(constant=0),
                    output_each=True,
                    description=f"AccumulatorReg {register.name}: add[{index}]",
                )
                self.circuit.entities.append(gate)
                self._attach(source.net, Endpoint(gate.id, Connector.INPUT))
            else:
                self._add_net_conflict(
                    source.net,
                    active.net,
                    f"AccumulatorReg {register.name}: vector add data/control isolation",
                )
                gate = ArithmeticCombinator(
                    id=self._take_entity_id(),
                    operation="*",
                    left=Operand(each=True, nets=(source.net,)),
                    right=Operand(signal=active.signal, nets=(active.net,)),
                    output_each=True,
                    description=f"AccumulatorReg {register.name}: gate add[{index}]",
                )
                self.circuit.entities.append(gate)
                gate_input = Endpoint(gate.id, Connector.INPUT)
                self._attach(source.net, gate_input)
                self._attach(active.net, gate_input)
            self._attach(self.state_memory_nets[register.name], Endpoint(gate.id, Connector.OUTPUT))

        memory_id = self.state_memory_ids[register.name]
        memory_net = self.state_memory_nets[register.name]
        if clear_never:
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="+",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(constant=0),
                output_each=True,
                description=f"AccumulatorReg {register.name}: vector memory",
            )
        elif retain is None:
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="*",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(constant=0),
                output_each=True,
                description=f"AccumulatorReg {register.name}: cleared vector memory",
            )
        else:
            self._add_net_conflict(
                memory_net,
                retain.net,
                f"AccumulatorReg {register.name}: memory data/clear isolation",
            )
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="*",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(signal=retain.signal, nets=(retain.net,)),
                output_each=True,
                description=f"AccumulatorReg {register.name}: vector memory",
            )
            self._attach(retain.net, Endpoint(memory_id, Connector.INPUT))
        self.circuit.entities.append(memory)
