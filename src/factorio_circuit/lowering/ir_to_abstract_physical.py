"""Lower supported semantic IR to the abstract physical Factorio IR."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations

from factorio_circuit.analysis.state_timing import StateTimingPlan, analyze_state_timing
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    AbstractSignal,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    Endpoint,
    InputPort,
    NetConflict,
    Operand,
    OutputPort,
    SignalConflict,
    SignalDomain,
    SignalRef,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    Input,
    InputSample,
    Select,
    Value,
    VectorConstant,
    VectorInput,
    VectorInputSample,
    VectorSignal,
    VectorValue,
    dependencies,
)
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    AccumulatorRegister,
    FreezeRegister,
    FreezeSet,
    VectorRegisterRead,
)
from factorio_circuit.optimize.compatibility import dynamic_operand
from factorio_circuit.optimize.partition import ArithmeticPartition, partition_arithmetic


@dataclass(frozen=True, slots=True)
class RealizedValue:
    signal: SignalRef
    net: int
    phase: int
    clean_single_lane: bool = True


@dataclass(frozen=True, slots=True)
class RealizedVector:
    net: int
    phase: int = 0


@dataclass(slots=True)
class _NetBuilder:
    signals: tuple[int, ...]
    endpoints: list[Endpoint]
    label: str | None = None
    fixed_signals: tuple[SignalId, ...] = ()
    carries_dynamic_vector: bool = False


class AbstractPhysicalLowerer:
    """Create target combinators while leaving late physical resources unresolved."""

    def __init__(
        self,
        module: CircuitModule,
        *,
        enable_packing: bool,
        state_timing: StateTimingPlan | None = None,
    ) -> None:
        self.module = module
        self.enable_packing = enable_packing
        self.state_timing = state_timing or analyze_state_timing(module)
        self.circuit = AbstractPhysicalCircuit(name=module.name)
        self.next_entity_id = 1
        self.next_signal_id = 1
        self.next_net_id = 1
        self.memo: dict[int, RealizedValue] = {}
        self.vector_memo: dict[int, RealizedVector] = {}
        self.state_outputs: dict[str, RealizedVector] = {}
        self.state_memory_ids: dict[str, int] = {}
        self.state_memory_nets: dict[str, int] = {}
        self.delay_cache: dict[tuple[int, SignalRef, int], RealizedValue] = {}
        self.net_builders: dict[int, _NetBuilder] = {}
        self.signal_conflict_keys: set[tuple[int, int]] = set()
        self.net_conflict_keys: set[tuple[int, int]] = set()
        self.use_count = self._count_uses()
        self.partition_for_op: dict[int, ArithmeticPartition] = {}
        if enable_packing:
            for partition in partition_arithmetic(module):
                if partition.key is not None and len(partition.operations) > 1:
                    for op in partition.operations:
                        self.partition_for_op[id(op)] = partition

    def lower(self) -> AbstractPhysicalCircuit:
        self._check_supported_scope()
        self._create_input_markers()
        self._reserve_state_outputs()
        self._create_state_components()
        realized_outputs: list[RealizedValue | RealizedVector] = []
        for value in self.module.output.values:
            if isinstance(
                value, (VectorInput, VectorInputSample, VectorConstant, VectorRegisterRead)
            ):
                realized_outputs.append(self.realize_vector(value))
            else:
                realized_outputs.append(self.realize(value))
        self._create_output_markers(realized_outputs)
        self.circuit.nets = [
            AbstractNet(
                id=net_id,
                signals=builder.signals,
                endpoints=tuple(builder.endpoints),
                label=builder.label,
                fixed_signals=builder.fixed_signals,
                carries_dynamic_vector=builder.carries_dynamic_vector,
            )
            for net_id, builder in sorted(self.net_builders.items())
        ]
        self.circuit.validate()
        return self.circuit

    def _check_supported_scope(self) -> None:
        unsupported_registers = [
            register
            for register in self.module.state_registers
            if not isinstance(register, (AccumulatorRegister, FreezeRegister))
        ]
        if unsupported_registers:
            names = ", ".join(register.name for register in unsupported_registers)
            raise ValueError(
                "abstract physical lowering supports AccumulatorReg and FreezeReg state; "
                f"unsupported register(s): {names}"
            )

        scalar_types = (Input, InputSample, Constant, BinaryOp, Compare, Select, VectorSignal)
        vector_types = (VectorInput, VectorInputSample, VectorConstant, VectorRegisterRead)
        if any(
            not isinstance(value, (*scalar_types, *vector_types))
            for value in self.module.output.values
        ):
            raise ValueError("abstract physical lowering encountered an unsupported output")

    def _reserve_state_outputs(self) -> None:
        """Reserve memory ids/nets before lowering possibly coupled state updates."""

        for register in self.module.state_registers:
            if isinstance(register, AccumulatorRegister):
                kind = "AccumulatorReg"
            elif isinstance(register, FreezeRegister):
                kind = "FreezeReg"
            else:  # pragma: no cover - scope check
                raise TypeError(register)
            memory_id = self._take_entity_id()
            memory_in = Endpoint(memory_id, Connector.INPUT)
            memory_out = Endpoint(memory_id, Connector.OUTPUT)
            memory_net = self._new_net(
                (),
                memory_in,
                label=f"{kind} {register.name}: memory network",
                carries_dynamic_vector=True,
            )
            self._attach(memory_net, memory_out)
            timing = self.state_timing.for_register(register)
            self.state_memory_ids[register.name] = memory_id
            self.state_memory_nets[register.name] = memory_net
            self.state_outputs[register.name] = RealizedVector(memory_net, timing.state_phase)

    def _create_state_components(self) -> None:
        for register in self.module.state_registers:
            if isinstance(register, AccumulatorRegister):
                self._lower_accumulator(register)
            elif isinstance(register, FreezeRegister):
                self._lower_freeze(register)
            else:  # pragma: no cover
                raise TypeError(register)

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
        memory_id = self.state_memory_ids[register.name]
        memory_net = self.state_memory_nets[register.name]

        clear_active: RealizedValue | None = None
        if clears:
            clear = self.realize(clears[0].when)
            active_signal = self._new_signal(f"AccumulatorReg {register.name}: clear-active")
            active = DeciderCombinator(
                id=self._take_entity_id(),
                comparator="==",
                left=Operand(signal=clear.signal, nets=(clear.net,)),
                right=Operand(constant=0),
                output_signal=active_signal,
                output_constant=1,
                description=f"AccumulatorReg {register.name}: active when clear=0",
            )
            self.circuit.entities.append(active)
            self._attach(clear.net, Endpoint(active.id, Connector.INPUT))
            active_net = self._new_net(
                (active_signal,),
                Endpoint(active.id, Connector.OUTPUT),
                label=f"AccumulatorReg {register.name}: clear-active",
            )
            clear_active = RealizedValue(active_signal, active_net, clear.phase + 1)

        for index, add in enumerate(adds):
            source = self.delay_vector_to(self.realize_vector(add.value), target_phase)
            if clear_active is not None and isinstance(add.when, Constant) and add.when.value != 0:
                # The default ``when=1`` adds no independent control.  Reuse clear-active
                # directly instead of materializing a constant signal that would be dead.
                gate_active = self.delay_to(clear_active, target_phase)
            else:
                add_active = self._realize_nonzero_control(
                    add.when,
                    description=f"AccumulatorReg {register.name}: add[{index}] enabled",
                )
                if clear_active is not None:
                    combined = self._emit_binary_from_realized("*", add_active, clear_active)
                    gate_active = self.delay_to(combined, target_phase)
                else:
                    gate_active = self.delay_to(add_active, target_phase)

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
                description=f"AccumulatorReg {register.name}: gate add[{index}]",
            )
            self.circuit.entities.append(gate)
            gate_input = Endpoint(gate.id, Connector.INPUT)
            self._attach(source.net, gate_input)
            self._attach(gate_active.net, gate_input)
            self._attach(memory_net, Endpoint(gate.id, Connector.OUTPUT))

        aligned_clear_active = (
            None if clear_active is None else self.delay_to(clear_active, target_phase)
        )
        if aligned_clear_active is None:
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="+",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(constant=0),
                output_each=True,
                description=f"AccumulatorReg {register.name}: vector memory",
            )
        else:
            self._add_net_conflict(
                memory_net,
                aligned_clear_active.net,
                f"AccumulatorReg {register.name}: memory data/clear isolation",
            )
            memory = ArithmeticCombinator(
                id=memory_id,
                operation="*",
                left=Operand(each=True, nets=(memory_net,)),
                right=Operand(
                    signal=aligned_clear_active.signal,
                    nets=(aligned_clear_active.net,),
                ),
                output_each=True,
                description=f"AccumulatorReg {register.name}: vector memory",
            )
            self._attach(aligned_clear_active.net, Endpoint(memory_id, Connector.INPUT))
        self.circuit.entities.append(memory)

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
        source = self.realize_vector(spec.value)
        control = self.realize(spec.when)

        pass_signal = self._new_signal(f"FreezeReg {register.name}: pass")
        pass_control = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=control.signal, nets=(control.net,)),
            right=Operand(constant=0),
            output_signal=pass_signal,
            output_constant=1,
            description=f"FreezeReg {register.name}: set!=0 -> pass",
        )
        self.circuit.entities.append(pass_control)
        self._attach(control.net, Endpoint(pass_control.id, Connector.INPUT))
        pass_net = self._new_net(
            (pass_signal,),
            Endpoint(pass_control.id, Connector.OUTPUT),
            label=f"FreezeReg {register.name}: pass",
        )

        hold_signal = self._new_signal(f"FreezeReg {register.name}: hold")
        hold_control = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="==",
            left=Operand(signal=control.signal, nets=(control.net,)),
            right=Operand(constant=0),
            output_signal=hold_signal,
            output_constant=1,
            description=f"FreezeReg {register.name}: set=0 -> hold",
        )
        self.circuit.entities.append(hold_control)
        self._attach(control.net, Endpoint(hold_control.id, Connector.INPUT))
        hold_net = self._new_net(
            (hold_signal,),
            Endpoint(hold_control.id, Connector.OUTPUT),
            label=f"FreezeReg {register.name}: hold",
        )

        control_phase = control.phase + 1
        pass_value = RealizedValue(pass_signal, pass_net, control_phase)
        hold_value = RealizedValue(hold_signal, hold_net, control_phase)
        timing = self.state_timing.for_register(register)
        target_phase = timing.transition_input_phase
        aligned_source = self.delay_vector_to(source, target_phase)
        aligned_pass = self.delay_to(pass_value, target_phase)
        aligned_hold = self.delay_to(hold_value, target_phase)

        self._add_net_conflict(
            aligned_source.net,
            aligned_pass.net,
            f"FreezeReg {register.name}: transparent data/control isolation",
        )
        gate = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation="*",
            left=Operand(each=True, nets=(aligned_source.net,)),
            right=Operand(signal=aligned_pass.signal, nets=(aligned_pass.net,)),
            output_each=True,
            description=f"FreezeReg {register.name}: transparent input gate",
        )
        self.circuit.entities.append(gate)
        gate_input = Endpoint(gate.id, Connector.INPUT)
        self._attach(aligned_source.net, gate_input)
        self._attach(aligned_pass.net, gate_input)

        memory_id = self.state_memory_ids[register.name]
        memory_net = self.state_memory_nets[register.name]
        self._attach(memory_net, Endpoint(gate.id, Connector.OUTPUT))
        self._add_net_conflict(
            memory_net,
            aligned_hold.net,
            f"FreezeReg {register.name}: memory data/hold isolation",
        )
        memory = ArithmeticCombinator(
            id=memory_id,
            operation="*",
            left=Operand(each=True, nets=(memory_net,)),
            right=Operand(signal=aligned_hold.signal, nets=(aligned_hold.net,)),
            output_each=True,
            description=f"FreezeReg {register.name}: vector memory",
        )
        self._attach(aligned_hold.net, Endpoint(memory_id, Connector.INPUT))
        self.circuit.entities.append(memory)

    def _create_input_markers(self) -> None:
        for item in self.module.inputs:
            signal = self._new_signal(item.name)
            marker = ConstantCombinator(
                id=self._take_entity_id(),
                description=f"INPUT {item.name} — signal allocated during physical synthesis",
                annotation_only=True,
            )
            self.circuit.entities.append(marker)
            endpoint = Endpoint(marker.id, Connector.SINGLE)
            net = self._new_net((signal,), endpoint, label=f"input {item.name}")
            self.circuit.inputs.append(InputPort(item.name, endpoint, signal))
            self.memo[id(item)] = RealizedValue(signal, net, 0)

        for vector_input in self.module.vector_inputs:
            marker = ConstantCombinator(
                id=self._take_entity_id(),
                description=(
                    f"INPUT {vector_input.name} — whole signal vector; edit any signals here"
                ),
                annotation_only=True,
            )
            self.circuit.entities.append(marker)
            endpoint = Endpoint(marker.id, Connector.SINGLE)
            net = self._new_net(
                (),
                endpoint,
                label=f"vector input {vector_input.name}",
                carries_dynamic_vector=True,
            )
            self.circuit.inputs.append(InputPort(vector_input.name, endpoint, None))
            self.vector_memo[id(vector_input)] = RealizedVector(net, 0)

    def _create_output_markers(self, outputs: list[RealizedValue | RealizedVector]) -> None:
        for index, (semantic, realized) in enumerate(
            zip(self.module.output.values, outputs, strict=True)
        ):
            declared_name = self.module.output.names[index] if self.module.output.names else None
            name = declared_name or getattr(semantic, "name", None) or f"out{index}"
            if isinstance(realized, RealizedVector):
                description = f"OUTPUT {name} — whole signal vector"
                signal = None
            else:
                description = f"OUTPUT {name} — phase +{realized.phase} tick(s)"
                signal = realized.signal
            marker = ConstantCombinator(
                id=self._take_entity_id(),
                description=description,
                annotation_only=True,
            )
            self.circuit.entities.append(marker)
            endpoint = Endpoint(marker.id, Connector.SINGLE)
            self._attach(realized.net, endpoint)
            self.circuit.outputs.append(OutputPort(name, endpoint, signal, realized.phase))

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        cached = self.vector_memo.get(id(value))
        if cached is not None:
            return cached
        if isinstance(value, VectorInput):  # pragma: no cover - initialized up-front
            raise ValueError(f"vector input {value.name!r} was not initialized")
        if isinstance(value, VectorInputSample):
            base = self.vector_memo.get(id(value.source))
            if base is None:
                raise ValueError(f"vector input {value.source.name!r} was not initialized")
            result = RealizedVector(base.net, value.offset)
        elif isinstance(value, VectorConstant):
            entity = ConstantCombinator(
                id=self._take_entity_id(),
                signals=value.signals,
                description=value.name or "constant signal vector",
            )
            self.circuit.entities.append(entity)
            fixed_signals = tuple(signal for signal, _count in value.signals)
            net = self._new_net(
                (),
                Endpoint(entity.id, Connector.SINGLE),
                label=value.name or "constant signal vector",
                fixed_signals=fixed_signals,
            )
            result = RealizedVector(net, 0)
        elif isinstance(value, VectorRegisterRead):
            try:
                state = self.state_outputs[value.register.name]
            except KeyError as exc:
                raise ValueError(
                    f"state register {value.register.name!r} was not reserved"
                ) from exc
            read_timing = self.state_timing.for_read(value)
            result = RealizedVector(state.net, read_timing.physical_phase)
        else:  # pragma: no cover
            raise TypeError(value)
        self.vector_memo[id(value)] = result
        return result

    def realize(self, value: Value) -> RealizedValue:
        cached = self.memo.get(id(value))
        if cached is not None:
            return cached

        if isinstance(value, Input):  # pragma: no cover - initialized up-front
            raise ValueError(f"input {value.name!r} was not initialized")
        if isinstance(value, InputSample):
            base = self.memo.get(id(value.source))
            if base is None:
                raise ValueError(f"input {value.source.name!r} was not initialized")
            result = RealizedValue(base.signal, base.net, value.offset, base.clean_single_lane)
        elif isinstance(value, Constant):
            result = self._materialize_constant(value)
        elif isinstance(value, BinaryOp):
            result = self._realize_binary(value)
        elif isinstance(value, Compare):
            result = self._realize_compare(value)
        elif isinstance(value, Select):
            result = self._realize_select(value)
        elif isinstance(value, VectorSignal):
            vector = self.realize_vector(value.vector)
            # A scalar lane read does not itself require a combinator.  Keep the fixed
            # Factorio signal identity attached to the source vector net and let the
            # eventual scalar consumer choose red/green separation.  If the lane later
            # needs phase alignment, delay_to() will materialize the required isolating
            # arithmetic combinator at that point.
            result = RealizedValue(
                value.signal,
                vector.net,
                vector.phase,
                clean_single_lane=False,
            )
        else:  # pragma: no cover
            raise TypeError(value)
        self.memo[id(value)] = result
        return result

    def _realize_nonzero_control(self, value: Value, *, description: str) -> RealizedValue:
        if isinstance(value, Constant):
            signal = self._new_signal(description)
            entity = ConstantCombinator(
                id=self._take_entity_id(),
                signals=((signal, int(value.value != 0)),),
                description=description,
            )
            self.circuit.entities.append(entity)
            net = self._new_net(
                (signal,),
                Endpoint(entity.id, Connector.SINGLE),
                label=description,
            )
            return RealizedValue(signal, net, 0)

        control = self.realize(value)
        signal = self._new_signal(description)
        control_entity = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=",
            left=Operand(signal=control.signal, nets=(control.net,)),
            right=Operand(constant=0),
            output_signal=signal,
            output_constant=1,
            description=description,
        )
        self.circuit.entities.append(control_entity)
        self._attach(control.net, Endpoint(control_entity.id, Connector.INPUT))
        net = self._new_net(
            (signal,),
            Endpoint(control_entity.id, Connector.OUTPUT),
            label=description,
        )
        return RealizedValue(signal, net, control.phase + 1)

    def _materialize_constant(self, value: Constant) -> RealizedValue:
        signal = self._new_signal(value.name or f"const {value.value}")
        entity = ConstantCombinator(
            id=self._take_entity_id(),
            signals=((signal, value.value),),
            description=f"constant {value.value}",
        )
        self.circuit.entities.append(entity)
        net = self._new_net(
            (signal,), Endpoint(entity.id, Connector.SINGLE), label=f"constant {value.value}"
        )
        return RealizedValue(signal, net, 0)

    def _realize_binary(self, op: BinaryOp) -> RealizedValue:
        partition = self.partition_for_op.get(id(op))
        if partition is not None and self._try_emit_partition(partition):
            return self.memo[id(op)]
        return self._emit_scalar_binary(op.op, op.left, op.right, description=op.name)

    def _try_emit_partition(self, partition: ArithmeticPartition) -> bool:
        if all(id(op) in self.memo for op in partition.operations):
            return True
        assert partition.key is not None

        dynamic_values = [dynamic_operand(op, partition.key) for op in partition.operations]
        realized = [self.realize(value) for value in dynamic_values]
        if any(self.use_count[id(value)] != 1 for value in dynamic_values):
            return False
        if any(not item.clean_single_lane for item in realized):
            return False

        target_phase = max(item.phase for item in realized)
        aligned = [self.delay_to(item, target_phase) for item in realized]
        input_nets = tuple(item.net for item in aligned)
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation=partition.key.operation,
            left=Operand(each=True, nets=input_nets),
            right=Operand(constant=partition.key.constant),
            output_each=True,
            description=(
                f"packed {len(partition.operations)}× {partition.key.operation} "
                f"{partition.key.constant}"
            ),
        )
        self.circuit.entities.append(entity)
        input_endpoint = Endpoint(entity.id, Connector.INPUT)
        for source in aligned:
            self._attach(source.net, input_endpoint)

        output_signals = tuple(
            source.signal for source in aligned if isinstance(source.signal, int)
        )
        output_net = self._new_net(
            output_signals,
            Endpoint(entity.id, Connector.OUTPUT),
            label="packed Each output",
        )
        phase = target_phase + 1
        for op, source in zip(partition.operations, aligned, strict=True):
            self.memo[id(op)] = RealizedValue(
                signal=source.signal,
                net=output_net,
                phase=phase,
                clean_single_lane=False,
            )
        return True

    def _realize_compare(self, comparison: Compare) -> RealizedValue:
        left_value, right_value, comparator = _normalize_compare(
            comparison.left, comparison.right, comparison.op
        )
        left = self._realize_operand_value(left_value)
        right = self._realize_operand_value(right_value)
        left, right, phase = self._align(left, right)

        out = self._new_signal(comparison.name or "compare")
        left_operand, right_operand = self._scalar_operand_layout(left, right)
        entity = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=comparator,
            left=left_operand,
            right=right_operand,
            output_signal=out,
            output_constant=1,
            description=comparison.name,
        )
        self.circuit.entities.append(entity)
        self._attach_dynamic_inputs(left, right, Endpoint(entity.id, Connector.INPUT))
        net = self._new_net(
            (out,), Endpoint(entity.id, Connector.OUTPUT), label=comparison.name or "compare"
        )
        return RealizedValue(out, net, phase + 1)

    def _realize_select(self, select: Select) -> RealizedValue:
        diff = self._emit_scalar_binary("-", select.when_true, select.when_false)
        gated = self._emit_binary_from_realized("*", diff, self.realize(select.condition))
        false_value = self._realize_operand_value(select.when_false)
        return self._emit_binary_from_operands("+", false_value, gated, description=select.name)

    def _emit_scalar_binary(
        self,
        operation: str,
        left_value: Value,
        right_value: Value,
        description: str | None = None,
    ) -> RealizedValue:
        left = self._realize_operand_value(left_value)
        right = self._realize_operand_value(right_value)
        return self._emit_binary_from_operands(operation, left, right, description=description)

    def _emit_binary_from_realized(
        self, operation: str, left: RealizedValue, right: RealizedValue
    ) -> RealizedValue:
        return self._emit_binary_from_operands(operation, left, right)

    def _emit_binary_from_operands(
        self,
        operation: str,
        left: RealizedValue | int,
        right: RealizedValue | int,
        *,
        description: str | None = None,
    ) -> RealizedValue:
        left, right, phase = self._align(left, right)
        out = self._new_signal(description or operation)
        left_operand, right_operand = self._scalar_operand_layout(left, right)
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation=operation,
            left=left_operand,
            right=right_operand,
            output_each=False,
            output_signal=out,
            description=description,
        )
        self.circuit.entities.append(entity)
        self._attach_dynamic_inputs(left, right, Endpoint(entity.id, Connector.INPUT))
        net = self._new_net(
            (out,), Endpoint(entity.id, Connector.OUTPUT), label=description or operation
        )
        return RealizedValue(out, net, phase + 1)

    def _scalar_operand_layout(
        self,
        left: RealizedValue | int,
        right: RealizedValue | int,
    ) -> tuple[Operand, Operand]:
        if (
            isinstance(left, RealizedValue)
            and isinstance(right, RealizedValue)
            and left.net != right.net
            and (not left.clean_single_lane or not right.clean_single_lane)
        ):
            self._add_net_conflict(
                left.net,
                right.net,
                "multi-lane source must remain electrically distinct at scalar consumer",
            )
        return self._operand(left), self._operand(right)

    def _attach_dynamic_inputs(
        self,
        left: RealizedValue | int,
        right: RealizedValue | int,
        endpoint: Endpoint,
    ) -> None:
        seen: set[int] = set()
        for value in (left, right):
            if isinstance(value, RealizedValue) and value.net not in seen:
                self._attach(value.net, endpoint)
                seen.add(value.net)

    def _realize_operand_value(self, value: Value) -> RealizedValue | int:
        if isinstance(value, Constant):
            return value.value
        return self.realize(value)

    def _align(
        self, left: RealizedValue | int, right: RealizedValue | int
    ) -> tuple[RealizedValue | int, RealizedValue | int, int]:
        phases = [item.phase for item in (left, right) if isinstance(item, RealizedValue)]
        target = max(phases, default=0)
        if isinstance(left, RealizedValue):
            left = self.delay_to(left, target)
        if isinstance(right, RealizedValue):
            right = self.delay_to(right, target)
        return left, right, target

    def delay_to(self, value: RealizedValue, target_phase: int) -> RealizedValue:
        if value.phase > target_phase:
            raise ValueError("cannot delay backwards in time")
        current = value
        while current.phase < target_phase:
            key = (current.net, current.signal, current.phase + 1)
            cached = self.delay_cache.get(key)
            if cached is not None:
                current = cached
                continue
            out = self._new_signal("phase delay")
            entity = ArithmeticCombinator(
                id=self._take_entity_id(),
                operation="+",
                left=Operand(signal=current.signal, nets=(current.net,)),
                right=Operand(constant=0),
                output_each=False,
                output_signal=out,
                description="phase alignment delay",
            )
            self.circuit.entities.append(entity)
            self._attach(current.net, Endpoint(entity.id, Connector.INPUT))
            output_net = self._new_net(
                (out,), Endpoint(entity.id, Connector.OUTPUT), label="phase alignment delay"
            )
            current = RealizedValue(out, output_net, current.phase + 1)
            self.delay_cache[key] = current
        return current

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        current = value
        while current.phase < target_phase:
            source = self.net_builders[current.net]
            entity = ArithmeticCombinator(
                id=self._take_entity_id(),
                operation="+",
                left=Operand(each=True, nets=(current.net,)),
                right=Operand(constant=0),
                output_each=True,
                description="vector phase alignment delay",
            )
            self.circuit.entities.append(entity)
            self._attach(current.net, Endpoint(entity.id, Connector.INPUT))
            output_net = self._new_net(
                source.signals,
                Endpoint(entity.id, Connector.OUTPUT),
                label="vector phase alignment delay",
                fixed_signals=source.fixed_signals,
                carries_dynamic_vector=source.carries_dynamic_vector,
            )
            current = RealizedVector(output_net, current.phase + 1)
        return current

    def _operand(self, value: RealizedValue | int) -> Operand:
        if isinstance(value, RealizedValue):
            return Operand(signal=value.signal, nets=(value.net,))
        return Operand(constant=value)

    def _new_signal(self, label: str | None = None) -> int:
        signal_id = self.next_signal_id
        self.next_signal_id += 1
        self.circuit.signals.append(
            AbstractSignal(signal_id, label=label, domain=SignalDomain.VIRTUAL)
        )
        return signal_id

    def _new_net(
        self,
        signals: tuple[int, ...],
        endpoint: Endpoint,
        *,
        label: str | None = None,
        fixed_signals: tuple[SignalId, ...] = (),
        carries_dynamic_vector: bool = False,
    ) -> int:
        net_id = self.next_net_id
        self.next_net_id += 1
        self.net_builders[net_id] = _NetBuilder(
            signals,
            [endpoint],
            label,
            fixed_signals,
            carries_dynamic_vector,
        )
        for left, right in combinations(signals, 2):
            self._add_signal_conflict(left, right, "signals coexist on one abstract electrical net")
        return net_id

    def _attach(self, net_id: int, endpoint: Endpoint) -> None:
        builder = self.net_builders[net_id]
        if endpoint not in builder.endpoints:
            builder.endpoints.append(endpoint)

    def _add_signal_conflict(self, left: int, right: int, reason: str) -> None:
        key = (left, right) if left < right else (right, left)
        if key in self.signal_conflict_keys:
            return
        self.signal_conflict_keys.add(key)
        self.circuit.signal_conflicts.append(SignalConflict(key[0], key[1], reason))

    def _add_net_conflict(self, left: int, right: int, reason: str) -> None:
        if left == right:
            return
        key = (left, right) if left < right else (right, left)
        if key in self.net_conflict_keys:
            return
        self.net_conflict_keys.add(key)
        self.circuit.net_conflicts.append(NetConflict(key[0], key[1], reason))

    def _take_entity_id(self) -> int:
        entity_id = self.next_entity_id
        self.next_entity_id += 1
        return entity_id

    def _count_uses(self) -> Counter[int]:
        counts: Counter[int] = Counter()
        for op in self.module.operations:
            for child in dependencies(op):
                counts[id(child)] += 1
        for output in self.module.output.values:
            counts[id(output)] += 1
        for state_op in self.module.state_operations:
            if isinstance(state_op, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
                counts[id(state_op.when)] += 1
        return counts


def lower_abstract_physical(
    module: CircuitModule,
    *,
    enable_packing: bool = True,
    state_timing: StateTimingPlan | None = None,
) -> AbstractPhysicalCircuit:
    """Lower the supported target subset to target-specific abstract physical IR."""

    return AbstractPhysicalLowerer(
        module, enable_packing=enable_packing, state_timing=state_timing
    ).lower()


def _normalize_compare(left: Value, right: Value, op: str) -> tuple[Value, Value, str]:
    """Keep constants on the right because that maps directly to decider configuration."""

    if not isinstance(left, Constant) or isinstance(right, Constant):
        return left, right, op
    swapped = {
        "==": "==",
        "!=": "!=",
        "<": ">",
        "<=": ">=",
        ">": "<",
        ">=": "<=",
    }[op]
    return right, left, swapped
