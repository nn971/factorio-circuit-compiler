"""Lower supported semantic IR to the abstract physical Factorio IR."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import cast

from factorio_circuit.analysis.latency import FACTORIO_LATENCY
from factorio_circuit.analysis.state_timing import (
    StateTimingPlan,
    analyze_normalized_state_timing,
)
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    AbstractSignal,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    DeciderCondition,
    DeciderOutput,
    Endpoint,
    InputPort,
    NetConflict,
    Operand,
    OutputPort,
    SignalAlias,
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
    FlowInput,
    FlowInputSample,
    FlowVectorInput,
    FlowVectorInputSample,
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
    reject_event_module,
    validate_canonical_module,
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
from factorio_circuit.optimize.partition import (
    ArithmeticPartition,
    PairwiseArithmeticPartition,
    partition_arithmetic,
    partition_pairwise_arithmetic,
)


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
        reject_event_module(module)
        validate_canonical_module(module)
        self.module = module
        self.enable_packing = enable_packing
        self.state_timing = state_timing or analyze_normalized_state_timing(module)
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
        self.signal_alias_keys: set[tuple[int, int]] = set()
        self.net_conflict_keys: set[tuple[int, int]] = set()
        self.use_count = self._count_uses()
        self.partition_for_op: dict[int, ArithmeticPartition] = {}
        self.pairwise_partition_for_op: dict[int, PairwiseArithmeticPartition] = {}
        self.shared_selects_by_condition: dict[int, tuple[Select, ...]] = {}
        self.output_value_ids = {id(value) for value in module.output.values}
        if enable_packing:
            for arithmetic_partition in partition_arithmetic(module):
                if (
                    arithmetic_partition.key is not None
                    and len(arithmetic_partition.operations) > 1
                ):
                    for op in arithmetic_partition.operations:
                        self.partition_for_op[id(op)] = arithmetic_partition
            for pairwise_partition in partition_pairwise_arithmetic(module):
                if len(pairwise_partition.operations) > 1:
                    for op in pairwise_partition.operations:
                        self.pairwise_partition_for_op[id(op)] = pairwise_partition
            select_groups: dict[int, list[Select]] = {}
            for operation in module.operations:
                if isinstance(operation, Select) and isinstance(operation.condition, Compare):
                    select_groups.setdefault(id(operation.condition), []).append(operation)
            self.shared_selects_by_condition = {
                key: tuple(values) for key, values in select_groups.items() if len(values) > 1
            }

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
                realized_outputs.append(self.realize(cast(Value, value)))
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
            clear_active = RealizedValue(
                active_signal,
                active_net,
                clear.phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "clear"),
            )

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

        control_phase = control.phase + FACTORIO_LATENCY.operation_latency(
            "scalar_binary", "control"
        )
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
        if isinstance(value, FlowVectorInput):
            base = self.vector_memo.get(id(value.source))
            if base is None:
                raise ValueError(f"vector input {value.name!r} was not initialized")
            result = base
        elif isinstance(value, VectorInput):  # pragma: no cover - initialized up-front
            raise ValueError(f"vector input {value.name!r} was not initialized")
        elif isinstance(value, (FlowVectorInputSample, VectorInputSample)):
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

        if isinstance(value, FlowInput):
            base = self.memo.get(id(value.source))
            if base is None:
                raise ValueError(f"input {value.name!r} was not initialized")
            result = base
        elif isinstance(value, Input):  # pragma: no cover - initialized up-front
            raise ValueError(f"input {value.name!r} was not initialized")
        elif isinstance(value, (FlowInputSample, InputSample)):
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
        return RealizedValue(
            signal,
            net,
            control.phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "control"),
        )

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
        pairwise = self.pairwise_partition_for_op.get(id(op))
        if pairwise is not None and self._try_emit_pairwise_partition(pairwise, op):
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
        phase = target_phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "delay")
        for op, source in zip(partition.operations, aligned, strict=True):
            self.memo[id(op)] = RealizedValue(
                signal=source.signal,
                net=output_net,
                phase=phase,
                clean_single_lane=False,
            )
        return True

    def _try_emit_pairwise_partition(
        self, partition: PairwiseArithmeticPartition, seed: BinaryOp
    ) -> bool:
        if id(seed) in self.memo:
            return True

        prepared: list[tuple[BinaryOp, RealizedValue, RealizedValue, int]] = []
        for candidate in partition.operations:
            if id(candidate) in self.memo:
                continue
            left = self._realize_operand_value(candidate.left)
            right = self._realize_operand_value(candidate.right)
            if not isinstance(left, RealizedValue) or not isinstance(right, RealizedValue):
                continue
            target_phase = max(left.phase, right.phase)
            prepared.append((candidate, left, right, target_phase))

        seed_record = next((item for item in prepared if item[0] is seed), None)
        if seed_record is None:
            return False
        seed_phase = seed_record[3]

        aligned: list[tuple[BinaryOp, RealizedValue, RealizedValue]] = []
        for candidate, left, right, target_phase in prepared:
            if target_phase != seed_phase:
                continue
            left = self.delay_to(left, target_phase)
            right = self.delay_to(right, target_phase)
            if not (left.clean_single_lane and right.clean_single_lane):
                continue
            if not (isinstance(left.signal, int) and isinstance(right.signal, int)):
                continue
            if left.net == right.net:
                continue
            aligned.append((candidate, left, right))

        seed_aligned = next((item for item in aligned if item[0] is seed), None)
        if seed_aligned is None:
            return False

        ordered = [seed_aligned, *(item for item in aligned if item[0] is not seed)]
        selected: list[tuple[BinaryOp, RealizedValue, RealizedValue]] = []
        for item in ordered:
            trial: list[tuple[BinaryOp, RealizedValue, RealizedValue]] = [*selected, item]
            if self._pairwise_group_is_feasible(trial):
                selected = trial

        if len(selected) < 2:
            return False

        left_nets = tuple(item[1].net for item in selected)
        right_nets = tuple(item[2].net for item in selected)
        entity = ArithmeticCombinator(
            id=self._take_entity_id(),
            operation=partition.operation,
            left=Operand(each=True, nets=left_nets),
            right=Operand(each=True, nets=right_nets),
            output_each=True,
            description=f"packed {len(selected)}× pairwise {partition.operation}",
        )
        self.circuit.entities.append(entity)
        input_endpoint = Endpoint(entity.id, Connector.INPUT)
        for net_id in dict.fromkeys((*left_nets, *right_nets)):
            self._attach(net_id, input_endpoint)
        for left_net in set(left_nets):
            for right_net in set(right_nets):
                self._add_net_conflict(
                    left_net,
                    right_net,
                    "pairwise Each operands must use opposite wire colors",
                )

        output_signals: list[int] = []
        for _candidate, left, right in selected:
            assert isinstance(left.signal, int)
            assert isinstance(right.signal, int)
            self._add_signal_alias(
                left.signal,
                right.signal,
                "pairwise Each operands must use the same concrete signal lane",
            )
            output_signals.append(left.signal)

        output_net = self._new_net(
            tuple(output_signals),
            Endpoint(entity.id, Connector.OUTPUT),
            label=f"packed pairwise {partition.operation} output",
        )
        phase = seed_phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "seed")
        for candidate, left, _right in selected:
            assert isinstance(left.signal, int)
            self.memo[id(candidate)] = RealizedValue(
                signal=left.signal,
                net=output_net,
                phase=phase,
                clean_single_lane=False,
            )
        return True

    def _pairwise_group_is_feasible(
        self, group: list[tuple[BinaryOp, RealizedValue, RealizedValue]]
    ) -> bool:
        left_nets = [item[1].net for item in group]
        right_nets = [item[2].net for item in group]
        if set(left_nets) & set(right_nets):
            return False

        left_signals: list[int] = []
        aliases: list[tuple[int, int]] = []
        for _candidate, left, right in group:
            if not (isinstance(left.signal, int) and isinstance(right.signal, int)):
                return False
            left_signals.append(left.signal)
            aliases.append((left.signal, right.signal))
        if len(set(left_signals)) != len(left_signals):
            return False

        lane_conflicts = list(combinations(left_signals, 2))
        if not self._signal_constraints_consistent(aliases, lane_conflicts):
            return False

        net_conflicts = [
            (left_net, right_net) for left_net in set(left_nets) for right_net in set(right_nets)
        ]
        return self._net_constraints_bipartite(net_conflicts)

    def _signal_constraints_consistent(
        self,
        aliases: list[tuple[int, int]],
        conflicts: list[tuple[int, int]],
    ) -> bool:
        signal_ids = [signal.id for signal in self.circuit.signals]
        parent = {signal_id: signal_id for signal_id in signal_ids}

        def find(signal_id: int) -> int:
            while parent[signal_id] != signal_id:
                parent[signal_id] = parent[parent[signal_id]]
                signal_id = parent[signal_id]
            return signal_id

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[max(left_root, right_root)] = min(left_root, right_root)

        for alias in self.circuit.signal_aliases:
            union(alias.left, alias.right)
        for left, right in aliases:
            union(left, right)
        for conflict in self.circuit.signal_conflicts:
            if find(conflict.left) == find(conflict.right):
                return False
        return all(find(left) != find(right) for left, right in conflicts)

    def _net_constraints_bipartite(self, extra: list[tuple[int, int]]) -> bool:
        adjacency: dict[int, set[int]] = {net_id: set() for net_id in self.net_builders}
        for conflict in self.circuit.net_conflicts:
            adjacency[conflict.left].add(conflict.right)
            adjacency[conflict.right].add(conflict.left)
        for left, right in extra:
            if left == right:
                return False
            adjacency[left].add(right)
            adjacency[right].add(left)

        colors: dict[int, int] = {}
        for start in sorted(adjacency):
            if start in colors:
                continue
            colors[start] = 0
            stack = [start]
            while stack:
                current = stack.pop()
                for neighbor in adjacency[current]:
                    expected = colors[current] ^ 1
                    if neighbor not in colors:
                        colors[neighbor] = expected
                        stack.append(neighbor)
                    elif colors[neighbor] != expected:
                        return False
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
        return RealizedValue(
            out, net, phase + FACTORIO_LATENCY.operation_latency("compare", comparison.op)
        )

    def _try_emit_shared_compare_selects(self, seed: Select) -> RealizedValue | None:
        condition = seed.condition
        if not isinstance(condition, Compare):
            return None
        group = self.shared_selects_by_condition.get(id(condition), ())
        if len(group) < 2 or any(id(item) in self.memo for item in group):
            return None
        if not all(id(item) in self.output_value_ids for item in group):
            return None

        prepared = self._prepare_compare_select(condition, group)
        if prepared is None:
            return None
        left, right, phase = prepared

        true_outputs: list[DeciderOutput] = []
        false_outputs: list[DeciderOutput] = []
        output_signals: list[int] = []
        for item in group:
            true_source = self._select_compare_source(item.when_true, condition, left, right)
            false_source = self._select_compare_source(item.when_false, condition, left, right)
            if true_source is None or false_source is None or true_source is false_source:
                return None
            output_signal = self._new_signal(item.name or "select")
            output_signals.append(output_signal)
            true_outputs.append(
                DeciderOutput(
                    signal=output_signal,
                    copy_count_from_input=True,
                    copy_count_nets=(true_source.net,),
                )
            )
            false_outputs.append(
                DeciderOutput(
                    signal=output_signal,
                    copy_count_from_input=True,
                    copy_count_nets=(false_source.net,),
                )
            )

        true_entity = self._emit_direct_compare_decider(
            condition,
            left,
            right,
            primary=true_outputs[0],
            additional=tuple(true_outputs[1:]),
            comparator=condition.op,
            description=f"fused {len(group)}-output compare/select true branch",
        )
        false_entity = self._emit_direct_compare_decider(
            condition,
            left,
            right,
            primary=false_outputs[0],
            additional=tuple(false_outputs[1:]),
            comparator=_complement_compare(condition.op),
            description=f"fused {len(group)}-output compare/select false branch",
        )
        self._commit_compare_lane_constraints(left, right)
        output_net = self._new_net(
            tuple(output_signals),
            Endpoint(true_entity.id, Connector.OUTPUT),
            label="fused compare/select outputs",
        )
        self._attach(output_net, Endpoint(false_entity.id, Connector.OUTPUT))
        for item, output_signal in zip(group, output_signals, strict=True):
            self.memo[id(item)] = RealizedValue(
                output_signal,
                output_net,
                phase + FACTORIO_LATENCY.operation_latency("compare", "select"),
                clean_single_lane=False,
            )
        return self.memo[id(seed)]

    def _try_emit_inline_compare_select(self, select: Select) -> RealizedValue | None:
        condition = select.condition
        if not isinstance(condition, Compare):
            return None
        if id(condition) not in self.shared_selects_by_condition:
            return None
        prepared = self._prepare_compare_select(condition, (select,))
        if prepared is None:
            return None
        left, right, phase = prepared
        true_source = self._select_compare_source(select.when_true, condition, left, right)
        false_source = self._select_compare_source(select.when_false, condition, left, right)
        if true_source is None or false_source is None or true_source is false_source:
            return None

        output_signal = self._new_signal(select.name or "select")
        true_output = DeciderOutput(
            signal=output_signal,
            copy_count_from_input=True,
            copy_count_nets=(true_source.net,),
        )
        false_output = DeciderOutput(
            signal=output_signal,
            copy_count_from_input=True,
            copy_count_nets=(false_source.net,),
        )
        true_entity = self._emit_direct_compare_decider(
            condition,
            left,
            right,
            primary=true_output,
            additional=(),
            comparator=condition.op,
            description=f"{select.name or 'select'}: direct compare true branch",
        )
        false_entity = self._emit_direct_compare_decider(
            condition,
            left,
            right,
            primary=false_output,
            additional=(),
            comparator=_complement_compare(condition.op),
            description=f"{select.name or 'select'}: direct compare false branch",
        )
        self._commit_compare_lane_constraints(left, right)
        output_net = self._new_net(
            (output_signal,),
            Endpoint(true_entity.id, Connector.OUTPUT),
            label=select.name or "direct compare/select",
        )
        self._attach(output_net, Endpoint(false_entity.id, Connector.OUTPUT))
        result = RealizedValue(
            output_signal,
            output_net,
            phase + FACTORIO_LATENCY.operation_latency("compare", "select"),
        )
        self.memo[id(select)] = result
        return result

    def _prepare_compare_select(
        self, condition: Compare, group: tuple[Select, ...]
    ) -> tuple[RealizedValue, RealizedValue, int] | None:
        if any(
            not (item.when_true is condition.left or item.when_true is condition.right)
            or not (item.when_false is condition.left or item.when_false is condition.right)
            for item in group
        ):
            return None
        left_value = self._realize_operand_value(condition.left)
        right_value = self._realize_operand_value(condition.right)
        if not isinstance(left_value, RealizedValue) or not isinstance(right_value, RealizedValue):
            return None
        left, right, phase = self._align(left_value, right_value)
        assert isinstance(left, RealizedValue) and isinstance(right, RealizedValue)
        if not (left.clean_single_lane and right.clean_single_lane):
            return None
        if not (isinstance(left.signal, int) and isinstance(right.signal, int)):
            return None
        if left.net == right.net:
            return None
        if not self._signal_constraints_consistent([(left.signal, right.signal)], []):
            return None
        if not self._net_constraints_bipartite([(left.net, right.net)]):
            return None
        return left, right, phase

    @staticmethod
    def _select_compare_source(
        value: Value,
        condition: Compare,
        left: RealizedValue | None,
        right: RealizedValue | None,
    ) -> RealizedValue | None:
        if value is condition.left:
            return left
        if value is condition.right:
            return right
        return None

    def _emit_direct_compare_decider(
        self,
        condition: Compare,
        left: RealizedValue,
        right: RealizedValue,
        *,
        primary: DeciderOutput,
        additional: tuple[DeciderOutput, ...],
        comparator: str,
        description: str,
    ) -> DeciderCombinator:
        entity = DeciderCombinator(
            id=self._take_entity_id(),
            comparator=comparator,
            left=Operand(each=True, nets=(left.net,)),
            right=Operand(each=True, nets=(right.net,)),
            output_signal=primary.signal,
            output_constant=primary.constant,
            output_copy_count_from_input=primary.copy_count_from_input,
            copy_count_nets=primary.copy_count_nets,
            additional_outputs=additional,
            description=description,
        )
        self.circuit.entities.append(entity)
        endpoint = Endpoint(entity.id, Connector.INPUT)
        self._attach(left.net, endpoint)
        self._attach(right.net, endpoint)
        return entity

    def _commit_compare_lane_constraints(self, left: RealizedValue, right: RealizedValue) -> None:
        assert isinstance(left.signal, int) and isinstance(right.signal, int)
        self._add_signal_alias(
            left.signal,
            right.signal,
            "direct Each comparison operands must use the same concrete signal lane",
        )
        self._add_net_conflict(
            left.net,
            right.net,
            "direct Each comparison operands must use opposite wire colors",
        )

    def _realize_select(self, select: Select) -> RealizedValue:
        if self.enable_packing and isinstance(select.condition, Compare):
            fused = self._try_emit_shared_compare_selects(select)
            if fused is not None:
                return fused
            inlined = self._try_emit_inline_compare_select(select)
            if inlined is not None:
                return inlined

        condition = self.realize(select.condition)
        when_true = self._realize_operand_value(select.when_true)
        when_false = self._realize_operand_value(select.when_false)

        phases = [condition.phase]
        phases.extend(
            value.phase for value in (when_true, when_false) if isinstance(value, RealizedValue)
        )
        target_phase = max(phases)
        condition = self.delay_to(condition, target_phase)
        if isinstance(when_true, RealizedValue):
            when_true = self.delay_to(when_true, target_phase)
        if isinstance(when_false, RealizedValue):
            when_false = self.delay_to(when_false, target_phase)

        if not (
            self._can_use_decider_mux_arm(condition, when_true)
            and self._can_use_decider_mux_arm(condition, when_false)
        ):
            diff = self._emit_binary_from_operands("-", when_true, when_false)
            gated = self._emit_binary_from_realized("*", diff, condition)
            return self._emit_binary_from_operands("+", when_false, gated, description=select.name)

        out = self._new_signal(select.name or "select")
        true_arm = self._emit_decider_mux_arm(
            condition,
            when_true,
            output_signal=out,
            active_when_true=True,
            description=f"{select.name or 'select'}: true arm",
        )
        false_arm = self._emit_decider_mux_arm(
            condition,
            when_false,
            output_signal=out,
            active_when_true=False,
            description=f"{select.name or 'select'}: false arm",
        )
        output_net = self._new_net(
            (out,),
            Endpoint(true_arm.id, Connector.OUTPUT),
            label=select.name or "select mux",
        )
        self._attach(output_net, Endpoint(false_arm.id, Connector.OUTPUT))
        return RealizedValue(
            out,
            output_net,
            target_phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "select"),
        )

    @staticmethod
    def _can_use_decider_mux_arm(condition: RealizedValue, arm: RealizedValue | int) -> bool:
        if isinstance(arm, int):
            return True
        return arm.clean_single_lane and arm.net != condition.net

    def _emit_decider_mux_arm(
        self,
        condition: RealizedValue,
        arm: RealizedValue | int,
        *,
        output_signal: int,
        active_when_true: bool,
        description: str,
    ) -> DeciderCombinator:
        additional_conditions: tuple[DeciderCondition, ...] = ()
        copy_count_nets: tuple[int, ...] = ()
        if isinstance(arm, RealizedValue):
            additional_conditions = (
                DeciderCondition(
                    comparator="!=",
                    left=Operand(each=True, nets=(arm.net,)),
                    right=Operand(constant=0),
                    compare_type="and",
                ),
            )
            copy_count_nets = (arm.net,)

        entity = DeciderCombinator(
            id=self._take_entity_id(),
            comparator="!=" if active_when_true else "==",
            left=Operand(signal=condition.signal, nets=(condition.net,)),
            right=Operand(constant=0),
            output_signal=output_signal,
            output_constant=arm if isinstance(arm, int) else 1,
            output_copy_count_from_input=isinstance(arm, RealizedValue),
            copy_count_nets=copy_count_nets,
            additional_conditions=additional_conditions,
            description=description,
        )
        self.circuit.entities.append(entity)
        input_endpoint = Endpoint(entity.id, Connector.INPUT)
        self._attach(condition.net, input_endpoint)
        if isinstance(arm, RealizedValue):
            self._attach(arm.net, input_endpoint)
            self._add_net_conflict(
                condition.net,
                arm.net,
                "decider mux control and Each data must use different wire colors",
            )
        return entity

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
        return RealizedValue(
            out, net, phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "binary")
        )

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
            key = (
                current.net,
                current.signal,
                current.phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "delay"),
            )
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
            current = RealizedValue(
                out,
                output_net,
                current.phase + FACTORIO_LATENCY.operation_latency("scalar_binary", "delay"),
            )
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
            current = RealizedVector(
                output_net,
                current.phase + FACTORIO_LATENCY.operation_latency("vector_binary", "delay"),
            )
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
        if left == right:
            return
        key = (left, right) if left < right else (right, left)
        if key in self.signal_conflict_keys:
            return
        self.signal_conflict_keys.add(key)
        self.circuit.signal_conflicts.append(SignalConflict(key[0], key[1], reason))

    def _add_signal_alias(self, left: int, right: int, reason: str) -> None:
        if left == right:
            return
        key = (left, right) if left < right else (right, left)
        if key in self.signal_alias_keys:
            return
        self.signal_alias_keys.add(key)
        self.circuit.signal_aliases.append(SignalAlias(key[0], key[1], reason))

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

    reject_event_module(module)
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


def _complement_compare(op: str) -> str:
    complements = {
        "==": "!=",
        "!=": "==",
        "<": ">=",
        "<=": ">",
        ">": "<=",
        ">=": "<",
    }
    try:
        return complements[op]
    except KeyError as exc:  # pragma: no cover - semantic IR validates comparators
        raise ValueError(f"unsupported comparison operator {op!r}") from exc
