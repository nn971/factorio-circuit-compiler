"""Whole-vector lowering plus periodic realization of multicycle state domains."""

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


class VectorLowerer(_Base):
    """Lower runtime-open vectors and state domains onto Factorio combinators."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        enable_packing: bool,
        state_timing: StateTimingPlan | None = None,
    ) -> None:
        super().__init__(module, enable_packing=enable_packing, state_timing=state_timing)
        self._clock_counters: dict[int, tuple[int, int, int]] = {}
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
        # The feedback net carries constant +1 plus the counter output.  After warm-up it cycles
        # through 1..period; compare the value present one tick before the state gate output.
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

    def _lower_freeze(self, register: FreezeRegister) -> None:
        timing = self.state_timing.for_register(register)
        if timing.period == 1:
            super()._lower_freeze(register)
            return

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
        target_phase = timing.transition_input_phase
        control_input_phase = target_phase - 1
        source = self.delay_vector_to(self.realize_vector(spec.value), target_phase)
        control = self.delay_to(self.realize(spec.when), control_input_phase)
        clock_equal, clock_net = self._clock_condition(register, target_phase, equal=True)
        clock_not_equal, _ = self._clock_condition(register, target_phase, equal=False)

        pass_signal = self._new_signal(f"FreezeReg {register.name}: periodic pass")
        pass_control = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=control.signal, nets=(control.net,)),
            right=Operand(constant=0),
            output_signal=pass_signal,
            output_constant=1,
            additional_conditions=(clock_equal,),
            description=f"FreezeReg {register.name}: set!=0 at logical boundary",
        )
        self.circuit.entities.append(pass_control)
        pass_input = Endpoint(pass_control.id, Connector.INPUT)
        self._attach(control.net, pass_input)
        self._attach(clock_net, pass_input)
        pass_net = self._new_net(
            (pass_signal,),
            Endpoint(pass_control.id, Connector.OUTPUT),
            label=f"FreezeReg {register.name}: periodic pass",
        )

        hold_signal = self._new_signal(f"FreezeReg {register.name}: periodic hold")
        hold_boundary = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="==",
            left=Operand(signal=control.signal, nets=(control.net,)),
            right=Operand(constant=0),
            output_signal=hold_signal,
            output_constant=1,
            additional_conditions=(clock_equal,),
            description=f"FreezeReg {register.name}: hold at inactive logical boundary",
        )
        hold_between = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=clock_not_equal.comparator,
            left=clock_not_equal.left,
            right=clock_not_equal.right,
            output_signal=hold_signal,
            output_constant=1,
            description=f"FreezeReg {register.name}: hold between logical boundaries",
        )
        self.circuit.entities.extend((hold_boundary, hold_between))
        boundary_input = Endpoint(hold_boundary.id, Connector.INPUT)
        self._attach(control.net, boundary_input)
        self._attach(clock_net, boundary_input)
        self._attach(clock_net, Endpoint(hold_between.id, Connector.INPUT))
        hold_net = self._new_net(
            (hold_signal,),
            Endpoint(hold_boundary.id, Connector.OUTPUT),
            label=f"FreezeReg {register.name}: periodic hold",
        )
        self._attach(hold_net, Endpoint(hold_between.id, Connector.OUTPUT))

        pass_value = RealizedValue(pass_signal, pass_net, target_phase)
        hold_value = RealizedValue(hold_signal, hold_net, target_phase)
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
            description=f"FreezeReg {register.name}: periodic input gate",
        )
        self.circuit.entities.append(gate)
        gate_input = Endpoint(gate.id, Connector.INPUT)
        self._attach(source.net, gate_input)
        self._attach(pass_value.net, gate_input)

        memory_id = self.state_memory_ids[register.name]
        memory_net = self.state_memory_nets[register.name]
        self._attach(memory_net, Endpoint(gate.id, Connector.OUTPUT))
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
            description=f"FreezeReg {register.name}: periodic vector memory",
        )
        self._attach(hold_value.net, Endpoint(memory_id, Connector.INPUT))
        self.circuit.entities.append(memory)

    def _lower_accumulator(self, register: AccumulatorRegister) -> None:
        timing = self.state_timing.for_register(register)
        if timing.period == 1:
            super()._lower_accumulator(register)
            return

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

        target_phase = timing.transition_input_phase
        control_input_phase = target_phase - 1
        clock_equal, clock_net = self._clock_condition(register, target_phase, equal=True)
        clock_not_equal, _ = self._clock_condition(register, target_phase, equal=False)
        clear = (
            None
            if not clears
            else self.delay_to(self.realize(clears[0].when), control_input_phase)
        )

        clear_active: RealizedValue | None = None
        if clear is not None:
            active_signal = self._new_signal(f"AccumulatorReg {register.name}: periodic clear-active")
            boundary = DeciderCombinator(
                id=self._take_entity_id(),
                comparator="==",
                left=Operand(signal=clear.signal, nets=(clear.net,)),
                right=Operand(constant=0),
                output_signal=active_signal,
                output_constant=1,
                additional_conditions=(clock_equal,),
                description=f"AccumulatorReg {register.name}: retain at logical boundary",
            )
            between = DeciderCombinator(
                id=self._take_entity_id(),
                comparator=clock_not_equal.comparator,
                left=clock_not_equal.left,
                right=clock_not_equal.right,
                output_signal=active_signal,
                output_constant=1,
                description=f"AccumulatorReg {register.name}: retain between logical boundaries",
            )
            self.circuit.entities.extend((boundary, between))
            boundary_input = Endpoint(boundary.id, Connector.INPUT)
            self._attach(clear.net, boundary_input)
            self._attach(clock_net, boundary_input)
            self._attach(clock_net, Endpoint(between.id, Connector.INPUT))
            active_net = self._new_net(
                (active_signal,),
                Endpoint(boundary.id, Connector.OUTPUT),
                label=f"AccumulatorReg {register.name}: periodic clear-active",
            )
            self._attach(active_net, Endpoint(between.id, Connector.OUTPUT))
            clear_active = RealizedValue(active_signal, active_net, target_phase)

        for index, add in enumerate(adds):
            source = self.delay_vector_to(self.realize_vector(add.value), target_phase)
            add_control = self.delay_to(self.realize(add.when), control_input_phase)
            additional = [clock_equal]
            if clear is not None:
                additional.insert(
                    0,
                    DeciderCondition(
                        comparator="==",
                        left=Operand(signal=clear.signal, nets=(clear.net,)),
                        right=Operand(constant=0),
                        compare_type="and",
                    ),
                )
            active_signal = self._new_signal(f"AccumulatorReg {register.name}: periodic add[{index}]")
            active = DeciderCombinator(
                id=self._take_entity_id(),
                comparator="!=",
                left=Operand(signal=add_control.signal, nets=(add_control.net,)),
                right=Operand(constant=0),
                output_signal=active_signal,
                output_constant=1,
                additional_conditions=tuple(additional),
                description=f"AccumulatorReg {register.name}: add[{index}] at logical boundary",
            )
            self.circuit.entities.append(active)
            active_input = Endpoint(active.id, Connector.INPUT)
            self._attach(add_control.net, active_input)
            if clear is not None:
                self._attach(clear.net, active_input)
            self._attach(clock_net, active_input)
            active_net = self._new_net(
                (active_signal,),
                Endpoint(active.id, Connector.OUTPUT),
                label=f"AccumulatorReg {register.name}: periodic add[{index}]",
            )
            gate_active = RealizedValue(active_signal, active_net, target_phase)

            self._add_net_conflict(
                source.net,
                gate_active.net,
                f"AccumulatorReg {register.name}: vector add data/control isolation",
            )
            gate = ArithmeticCombinator(
                id=self._take_entity_id(),
                operation="*",
                left=Operand(each=True, nets=(source.net,)),
                right=Operand(signal=gate_active.signal, nets=(gate_active.net,)),
                output_each=True,
                description=f"AccumulatorReg {register.name}: periodic gate add[{index}]",
            )
            self.circuit.entities.append(gate)
            gate_input = Endpoint(gate.id, Connector.INPUT)
            self._attach(source.net, gate_input)
            self._attach(gate_active.net, gate_input)
            self._attach(self.state_memory_nets[register.name], Endpoint(gate.id, Connector.OUTPUT))

        memory_id = self.state_memory_ids[register.name]
        memory_net = self.state_memory_nets[register.name]
        if clear_active is None:
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="+",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(constant=0),
                output_each=True,
                description=f"AccumulatorReg {register.name}: periodic vector memory",
            )
        else:
            self._add_net_conflict(
                memory_net,
                clear_active.net,
                f"AccumulatorReg {register.name}: memory data/clear isolation",
            )
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="*",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(signal=clear_active.signal, nets=(clear_active.net,)),
                output_each=True,
                description=f"AccumulatorReg {register.name}: periodic vector memory",
            )
            self._attach(clear_active.net, Endpoint(memory_id, Connector.INPUT))
        self.circuit.entities.append(memory)
