"""Lower semantic IR to a connected, phase-correct Factorio combinator graph."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from factorio_circuit.analysis.state_timing import StateTimingPlan, analyze_state_timing
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    InputPort,
    Operand,
    OutputPort,
    PhysicalCircuit,
    SignalId,
    WireColor,
    WireConnection,
    WireEndpoint,
)
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
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL


@dataclass(slots=True)
class SignalAllocator:
    pool: tuple[SignalId, ...] = DEFAULT_VIRTUAL_SIGNAL_POOL
    cursor: int = 0

    def allocate(self) -> SignalId:
        if self.cursor >= len(self.pool):
            raise ValueError(
                "prototype signal pool exhausted; prototype-driven signal allocation is a later milestone"
            )
        result = self.pool[self.cursor]
        self.cursor += 1
        return result


@dataclass(frozen=True, slots=True)
class RealizedValue:
    signal: SignalId
    endpoint: WireEndpoint
    phase: int
    clean_single_lane: bool = True


@dataclass(frozen=True, slots=True)
class RealizedVector:
    endpoint: WireEndpoint
    phase: int = 0


class PhysicalLowerer:
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
        self.circuit = PhysicalCircuit(name=module.name)
        self.allocator = SignalAllocator()
        self.next_entity_id = 1
        self.memo: dict[int, RealizedValue] = {}
        self.delay_cache: dict[tuple[WireEndpoint, SignalId, int], RealizedValue] = {}
        self.connection_keys: set[tuple[WireEndpoint, WireEndpoint, WireColor]] = set()
        self.use_count = self._count_uses()
        self.partition_for_op: dict[int, ArithmeticPartition] = {}
        self.vector_memo: dict[int, RealizedVector] = {}
        self.state_outputs: dict[str, RealizedVector] = {}
        self.state_memory_ids: dict[str, int] = {}
        if enable_packing:
            for partition in partition_arithmetic(module):
                if partition.key is not None and len(partition.operations) > 1:
                    for op in partition.operations:
                        self.partition_for_op[id(op)] = partition

    def lower(self) -> PhysicalCircuit:
        self._create_input_markers()
        self._reserve_state_outputs()
        self._create_state_components()
        realized_outputs: list[RealizedValue | RealizedVector] = []
        for value in self.module.output.values:
            if isinstance(value, (VectorInput, VectorInputSample, VectorConstant, VectorRegisterRead)):
                realized_outputs.append(self.realize_vector(value))
            else:
                realized_outputs.append(self.realize(value))
        self._create_output_markers(realized_outputs)
        return self.circuit

    def _reserve_state_outputs(self) -> None:
        """Reserve memory ids before wiring so mutually coupled registers can reference each other."""

        for register in self.module.state_registers:
            memory_id = self._take_id()
            self.state_memory_ids[register.name] = memory_id
            timing = self.state_timing.for_register(register)
            self.state_outputs[register.name] = RealizedVector(
                WireEndpoint(memory_id, Connector.OUTPUT), timing.state_phase
            )

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

        clear_active: RealizedValue | None = None
        if clears:
            clear = self.realize(clears[0].when)
            active_signal = SignalId("virtual", "signal-green")
            active = self._add_entity(
                DeciderCombinator(
                    id=self._take_id(),
                    comparator="==",
                    left=Operand(signal=clear.signal),
                    right=Operand(constant=0),
                    output_signal=active_signal,
                    output_constant=1,
                    description=f"AccumulatorReg {register.name}: active when clear=0",
                )
            )
            self._connect(clear.endpoint, WireEndpoint(active.id, Connector.INPUT))
            clear_active = RealizedValue(
                active_signal, WireEndpoint(active.id, Connector.OUTPUT), clear.phase + 1
            )

        gated_outputs: list[WireEndpoint] = []
        for index, add in enumerate(adds):
            source = self.delay_vector_to(self.realize_vector(add.value), target_phase)
            if (
                clear_active is not None
                and isinstance(add.when, Constant)
                and add.when.value != 0
            ):
                # The default ``when=1`` adds no independent control. Reuse clear-active
                # directly instead of materializing a constant signal that would be dead.
                gate_active = self.delay_to(clear_active, target_phase)
            else:
                add_active = self._realize_nonzero_control(
                    add.when,
                    description=f"AccumulatorReg {register.name}: add[{index}] enabled",
                )
                if clear_active is not None:
                    # Combining a dynamic add-enable with clear is supported, but costs a scalar
                    # combinator before the vector gate.
                    combined = self._emit_binary_from_realized("*", add_active, clear_active)
                    gate_active = self.delay_to(combined, target_phase)
                else:
                    gate_active = self.delay_to(add_active, target_phase)

            gate = self._add_entity(
                ArithmeticCombinator(
                    id=self._take_id(),
                    operation="*",
                    left=Operand(each=True, networks=(WireColor.RED,)),
                    right=Operand(signal=gate_active.signal, networks=(WireColor.GREEN,)),
                    output_each=True,
                    description=f"AccumulatorReg {register.name}: gate add[{index}]",
                )
            )
            gate_input = WireEndpoint(gate.id, Connector.INPUT)
            self._connect(source.endpoint, gate_input, color=WireColor.RED)
            self._connect(gate_active.endpoint, gate_input, color=WireColor.GREEN)
            gated_outputs.append(WireEndpoint(gate.id, Connector.OUTPUT))

        memory_id = self.state_memory_ids[register.name]
        aligned_clear_active = (
            None if clear_active is None else self.delay_to(clear_active, target_phase)
        )
        if aligned_clear_active is None:
            memory = self._add_entity(
                ArithmeticCombinator(
                    id=memory_id,
                    operation="+",
                    left=Operand(each=True, networks=(WireColor.RED,)),
                    right=Operand(constant=0),
                    output_each=True,
                    description=f"AccumulatorReg {register.name}: vector memory",
                )
            )
        else:
            memory = self._add_entity(
                ArithmeticCombinator(
                    id=memory_id,
                    operation="*",
                    left=Operand(each=True, networks=(WireColor.RED,)),
                    right=Operand(signal=aligned_clear_active.signal, networks=(WireColor.GREEN,)),
                    output_each=True,
                    description=f"AccumulatorReg {register.name}: vector memory",
                )
            )

        memory_in = WireEndpoint(memory.id, Connector.INPUT)
        memory_out = WireEndpoint(memory.id, Connector.OUTPUT)
        for endpoint in gated_outputs:
            self._connect(endpoint, memory_in, color=WireColor.RED)
        if aligned_clear_active is not None:
            self._connect(aligned_clear_active.endpoint, memory_in, color=WireColor.GREEN)
        self._connect(memory_out, memory_in, color=WireColor.RED)

    def _lower_freeze(self, register: FreezeRegister) -> None:
        sets = [
            op for op in self.module.state_operations
            if isinstance(op, FreezeSet) and op.register == register
        ]
        if len(sets) != 1:
            raise ValueError(f"FreezeReg {register.name!r} requires exactly one .set(data, when=...) call")
        spec = sets[0]
        source = self.realize_vector(spec.value)
        control = self.realize(spec.when)

        pass_signal = self.allocator.allocate()
        pass_control = self._add_entity(
            DeciderCombinator(
                id=self._take_id(), comparator="!=",
                left=Operand(signal=control.signal), right=Operand(constant=0),
                output_signal=pass_signal, output_constant=1,
                description=f"FreezeReg {register.name}: set!=0 -> pass",
            )
        )
        hold_signal = SignalId("virtual", "signal-green")
        hold_control = self._add_entity(
            DeciderCombinator(
                id=self._take_id(), comparator="==",
                left=Operand(signal=control.signal), right=Operand(constant=0),
                output_signal=hold_signal, output_constant=1,
                description=f"FreezeReg {register.name}: set=0 -> hold",
            )
        )
        for entity in (pass_control, hold_control):
            self._connect(control.endpoint, WireEndpoint(entity.id, Connector.INPUT))

        control_phase = control.phase + 1
        pass_value = RealizedValue(
            pass_signal, WireEndpoint(pass_control.id, Connector.OUTPUT), control_phase
        )
        hold_value = RealizedValue(
            hold_signal, WireEndpoint(hold_control.id, Connector.OUTPUT), control_phase
        )
        timing = self.state_timing.for_register(register)
        target_phase = timing.transition_input_phase
        aligned_source = self.delay_vector_to(source, target_phase)
        aligned_pass = self.delay_to(pass_value, target_phase)
        aligned_hold = self.delay_to(hold_value, target_phase)
        gate = self._add_entity(
            ArithmeticCombinator(
                id=self._take_id(), operation="*",
                left=Operand(each=True, networks=(WireColor.RED,)),
                right=Operand(signal=pass_signal, networks=(WireColor.GREEN,)),
                output_each=True, description=f"FreezeReg {register.name}: transparent input gate",
            )
        )
        self._connect(aligned_source.endpoint, WireEndpoint(gate.id, Connector.INPUT), color=WireColor.RED)
        self._connect(aligned_pass.endpoint, WireEndpoint(gate.id, Connector.INPUT), color=WireColor.GREEN)

        memory = self._add_entity(
            ArithmeticCombinator(
                id=self.state_memory_ids[register.name], operation="*",
                left=Operand(each=True, networks=(WireColor.RED,)),
                right=Operand(signal=hold_signal, networks=(WireColor.GREEN,)),
                output_each=True, description=f"FreezeReg {register.name}: vector memory",
            )
        )
        memory_in = WireEndpoint(memory.id, Connector.INPUT)
        memory_out = WireEndpoint(memory.id, Connector.OUTPUT)
        self._connect(WireEndpoint(gate.id, Connector.OUTPUT), memory_in, color=WireColor.RED)
        self._connect(aligned_hold.endpoint, memory_in, color=WireColor.GREEN)
        self._connect(memory_out, memory_in, color=WireColor.RED)

    def _create_input_markers(self) -> None:
        for item in self.module.inputs:
            signal = self.allocator.allocate()
            marker = self._add_entity(
                ConstantCombinator(
                    id=self._take_id(),
                    description=f"INPUT {item.name} — inject value on [{signal.name}] here",
                    annotation_only=True,
                )
            )
            self.circuit.inputs.append(InputPort(item.name, marker.id, signal))
            self.memo[id(item)] = RealizedValue(
                signal=signal, endpoint=WireEndpoint(marker.id, Connector.SINGLE), phase=0
            )
        for item in self.module.vector_inputs:
            marker = self._add_entity(
                ConstantCombinator(
                    id=self._take_id(),
                    description=f"INPUT {item.name} — whole signal vector; edit any signals here",
                    annotation_only=True,
                )
            )
            self.circuit.inputs.append(InputPort(item.name, marker.id, None))
            self.vector_memo[id(item)] = RealizedVector(
                endpoint=WireEndpoint(marker.id, Connector.SINGLE), phase=0
            )

    def _create_output_markers(
        self, outputs: list[RealizedValue | RealizedVector]
    ) -> None:
        for index, (semantic, realized) in enumerate(
            zip(self.module.output.values, outputs, strict=True)
        ):
            declared_name = (
                self.module.output.names[index] if self.module.output.names else None
            )
            name = declared_name or getattr(semantic, "name", None) or f"out{index}"
            if isinstance(realized, RealizedVector):
                description = f"OUTPUT {name} — whole signal vector"
                signal = None
                phase = realized.phase
            else:
                description = f"OUTPUT {name} — [{realized.signal.name}], phase +{realized.phase} tick(s)"
                signal = realized.signal
                phase = realized.phase
            marker = self._add_entity(
                ConstantCombinator(
                    id=self._take_id(), description=description, annotation_only=True
                )
            )
            self._connect(realized.endpoint, WireEndpoint(marker.id, Connector.SINGLE))
            self.circuit.outputs.append(OutputPort(name, marker.id, signal, phase))

    def realize_vector(self, value: VectorValue) -> RealizedVector:
        cached = self.vector_memo.get(id(value))
        if cached is not None:
            return cached
        if isinstance(value, VectorInput):
            raise ValueError(f"vector input {value.name!r} was not initialized")
        if isinstance(value, VectorInputSample):
            base = self.vector_memo.get(id(value.source))
            if base is None:
                raise ValueError(f"vector input {value.source.name!r} was not initialized")
            result = RealizedVector(base.endpoint, value.offset)
            self.vector_memo[id(value)] = result
            return result
        if isinstance(value, VectorConstant):
            entity = self._add_entity(
                ConstantCombinator(
                    id=self._take_id(),
                    signals=value.signals,
                    description=value.name or "constant signal vector",
                )
            )
            result = RealizedVector(WireEndpoint(entity.id, Connector.SINGLE), 0)
            self.vector_memo[id(value)] = result
            return result
        if isinstance(value, VectorRegisterRead):
            try:
                result = self.state_outputs[value.register.name]
            except KeyError as exc:
                raise ValueError(
                    "feeding one state register from another is deferred in the first vector-state milestone"
                ) from exc
            read_timing = self.state_timing.for_read(value)
            result = RealizedVector(result.endpoint, read_timing.physical_phase)
            self.vector_memo[id(value)] = result
            return result
        raise TypeError(value)

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
            result = RealizedValue(
                base.signal, base.endpoint, value.offset, base.clean_single_lane
            )
        elif isinstance(value, Constant):
            result = self._materialize_constant(value)
        elif isinstance(value, BinaryOp):
            result = self._realize_binary(value)
        elif isinstance(value, Compare):
            result = self._realize_compare(value)
        elif isinstance(value, Select):
            result = self._realize_select(value)
        elif isinstance(value, VectorSignal):
            # A vector value is a concrete red-wire network.  Expose one lane through a real
            # combinator instead of treating the lane as a freely re-wireable scalar source:
            # directly reusing the vector endpoint on another wire color would observe a different
            # Factorio network (and for feedback state can miss the transparent/pass contribution).
            # The extractor also gives later scalar logic a private compiler-allocated lane, so two
            # state vectors can safely participate in one scalar expression without merging their
            # feedback networks.
            vector = self.realize_vector(value.vector)
            out = self.allocator.allocate()
            entity = self._add_entity(
                ArithmeticCombinator(
                    id=self._take_id(),
                    operation="+",
                    left=Operand(signal=value.signal, networks=(WireColor.RED,)),
                    right=Operand(constant=0),
                    output_each=False,
                    output_signal=out,
                    description=f"extract [{value.signal.name}] from vector",
                )
            )
            self._connect(
                vector.endpoint,
                WireEndpoint(entity.id, Connector.INPUT),
                color=WireColor.RED,
            )
            result = RealizedValue(
                out,
                WireEndpoint(entity.id, Connector.OUTPUT),
                vector.phase + 1,
                clean_single_lane=True,
            )
        else:  # pragma: no cover
            raise TypeError(value)
        self.memo[id(value)] = result
        return result

    def _realize_nonzero_control(
        self, value: Value, *, description: str
    ) -> RealizedValue:
        if isinstance(value, Constant):
            signal = self.allocator.allocate()
            entity = self._add_entity(
                ConstantCombinator(
                    id=self._take_id(),
                    signals=((signal, int(value.value != 0)),),
                    description=description,
                )
            )
            return RealizedValue(signal, WireEndpoint(entity.id, Connector.SINGLE), 0)

        control = self.realize(value)
        signal = self.allocator.allocate()
        entity = self._add_entity(
            DeciderCombinator(
                id=self._take_id(),
                comparator="!=",
                left=Operand(signal=control.signal),
                right=Operand(constant=0),
                output_signal=signal,
                output_constant=1,
                description=description,
            )
        )
        self._connect(control.endpoint, WireEndpoint(entity.id, Connector.INPUT))
        return RealizedValue(
            signal, WireEndpoint(entity.id, Connector.OUTPUT), control.phase + 1
        )

    def _materialize_constant(self, value: Constant) -> RealizedValue:
        signal = self.allocator.allocate()
        entity = self._add_entity(
            ConstantCombinator(
                id=self._take_id(),
                signals=((signal, value.value),),
                description=f"constant {value.value}",
            )
        )
        return RealizedValue(signal, WireEndpoint(entity.id, Connector.SINGLE), 0)

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

        # Conservative first safety rule for Each packing: every source lane must be private to this
        # operation and come from a network that carries exactly one logical lane. This avoids
        # accidentally applying Each to unrelated signals after Factorio wire-network merging.
        if any(self.use_count[id(value)] != 1 for value in dynamic_values):
            return False
        if any(not item.clean_single_lane for item in realized):
            return False

        target_phase = max(item.phase for item in realized)
        aligned = [self.delay_to(item, target_phase) for item in realized]

        entity = self._add_entity(
            ArithmeticCombinator(
                id=self._take_id(),
                operation=partition.key.operation,
                left=Operand(each=True),
                right=Operand(constant=partition.key.constant),
                output_each=True,
                description=f"packed {len(partition.operations)}× {partition.key.operation} {partition.key.constant}",
            )
        )
        input_endpoint = WireEndpoint(entity.id, Connector.INPUT)
        output_endpoint = WireEndpoint(entity.id, Connector.OUTPUT)
        for source in aligned:
            self._connect(source.endpoint, input_endpoint)

        phase = target_phase + 1
        for op, source in zip(partition.operations, aligned, strict=True):
            # Each→Each preserves the lane identity while moving it onto the output network.
            self.memo[id(op)] = RealizedValue(
                signal=source.signal,
                endpoint=output_endpoint,
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

        out = self.allocator.allocate()
        left_operand, right_operand, wiring = self._scalar_operand_layout(left, right)
        entity = self._add_entity(
            DeciderCombinator(
                id=self._take_id(),
                comparator=comparator,
                left=left_operand,
                right=right_operand,
                output_signal=out,
                output_constant=1,
                description=comparison.name,
            )
        )
        input_endpoint = WireEndpoint(entity.id, Connector.INPUT)
        for value, color in wiring:
            self._connect_dynamic(value, input_endpoint, color=color)
        return RealizedValue(out, WireEndpoint(entity.id, Connector.OUTPUT), phase + 1)

    def _realize_select(self, select: Select) -> RealizedValue:
        # Implement a scalar mux in i32 arithmetic:
        # false + condition * (true - false). Compare nodes produce condition ∈ {0,1}.
        diff = self._emit_scalar_binary("-", select.when_true, select.when_false)
        gated = self._emit_binary_from_realized("*", diff, self.realize(select.condition))
        false_value = self._realize_operand_value(select.when_false)
        result = self._emit_binary_from_operands("+", false_value, gated, description=select.name)
        return result

    def _emit_scalar_binary(
        self, operation: str, left_value: Value, right_value: Value, description: str | None = None
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
        out = self.allocator.allocate()
        left_operand, right_operand, wiring = self._scalar_operand_layout(left, right)
        entity = self._add_entity(
            ArithmeticCombinator(
                id=self._take_id(),
                operation=operation,
                left=left_operand,
                right=right_operand,
                output_each=False,
                output_signal=out,
                description=description,
            )
        )
        input_endpoint = WireEndpoint(entity.id, Connector.INPUT)
        for value, color in wiring:
            self._connect_dynamic(value, input_endpoint, color=color)
        return RealizedValue(out, WireEndpoint(entity.id, Connector.OUTPUT), phase + 1)

    def _scalar_operand_layout(
        self,
        left: RealizedValue | int,
        right: RealizedValue | int,
    ) -> tuple[
        Operand,
        Operand,
        list[tuple[RealizedValue | int, WireColor]],
    ]:
        """Choose wire colors without accidentally merging whole-vector source networks.

        Ordinary scalar values live on private compiler-allocated lanes, so sharing one red input
        network is safe.  A ``VectorSignal`` view, however, points directly at a whole-vector wire
        network that may also be a register feedback network.  Joining another dynamic source to
        that same red network would electrically merge the source networks and can mutate state.

        A binary/compare combinator has exactly two independent wire colors, so keep the operands on
        red and green whenever either dynamic operand is vector-backed.
        """

        isolate = (
            isinstance(left, RealizedValue)
            and isinstance(right, RealizedValue)
            and (not left.clean_single_lane or not right.clean_single_lane)
        )
        if isolate:
            return (
                self._operand(left, networks=(WireColor.RED,)),
                self._operand(right, networks=(WireColor.GREEN,)),
                [(left, WireColor.RED), (right, WireColor.GREEN)],
            )
        wiring: list[tuple[RealizedValue | int, WireColor]] = []
        if isinstance(left, RealizedValue):
            wiring.append((left, WireColor.RED))
        if isinstance(right, RealizedValue):
            wiring.append((right, WireColor.RED))
        return self._operand(left), self._operand(right), wiring

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
            key = (current.endpoint, current.signal, current.phase + 1)
            cached = self.delay_cache.get(key)
            if cached is not None:
                current = cached
                continue
            out = self.allocator.allocate()
            entity = self._add_entity(
                ArithmeticCombinator(
                    id=self._take_id(),
                    operation="+",
                    left=Operand(signal=current.signal),
                    right=Operand(constant=0),
                    output_each=False,
                    output_signal=out,
                    description="phase alignment delay",
                )
            )
            self._connect(current.endpoint, WireEndpoint(entity.id, Connector.INPUT))
            current = RealizedValue(
                out,
                WireEndpoint(entity.id, Connector.OUTPUT),
                current.phase + 1,
            )
            self.delay_cache[key] = current
        return current

    def delay_vector_to(self, value: RealizedVector, target_phase: int) -> RealizedVector:
        if value.phase > target_phase:
            raise ValueError("cannot delay vector backwards in time")
        current = value
        while current.phase < target_phase:
            entity = self._add_entity(
                ArithmeticCombinator(
                    id=self._take_id(), operation="+",
                    left=Operand(each=True, networks=(WireColor.RED,)),
                    right=Operand(constant=0), output_each=True,
                    description="vector phase alignment delay",
                )
            )
            self._connect(current.endpoint, WireEndpoint(entity.id, Connector.INPUT), color=WireColor.RED)
            current = RealizedVector(WireEndpoint(entity.id, Connector.OUTPUT), current.phase + 1)
        return current

    def _realize_as_signal(
        self, value: Value, target_signal: SignalId, target_phase: int, description: str
    ) -> RealizedValue:
        if isinstance(value, Constant) and target_phase == 0:
            entity = self._add_entity(
                ConstantCombinator(
                    id=self._take_id(),
                    signals=((target_signal, value.value),),
                    description=description,
                )
            )
            return RealizedValue(target_signal, WireEndpoint(entity.id, Connector.SINGLE), 0)

        realized = self.realize(value)
        if realized.signal == target_signal:
            return self.delay_to(realized, target_phase)
        if target_phase <= realized.phase:
            raise ValueError(
                f"{description} is scheduled at register tick {target_phase}, but its value is only "
                f"available at phase {realized.phase} and signal renaming needs one more tick"
            )
        source = self.delay_to(realized, target_phase - 1)
        entity = self._add_entity(
            ArithmeticCombinator(
                id=self._take_id(),
                operation="+",
                left=Operand(signal=source.signal),
                right=Operand(constant=0),
                output_each=False,
                output_signal=target_signal,
                description=description,
            )
        )
        self._connect(source.endpoint, WireEndpoint(entity.id, Connector.INPUT))
        return RealizedValue(target_signal, WireEndpoint(entity.id, Connector.OUTPUT), target_phase)

    def _operand(
        self,
        value: RealizedValue | int,
        *,
        networks: tuple[WireColor, ...] | None = None,
    ) -> Operand:
        return (
            Operand(signal=value.signal, networks=networks)
            if isinstance(value, RealizedValue)
            else Operand(constant=value)
        )

    def _connect_dynamic(
        self,
        value: RealizedValue | int,
        target: WireEndpoint,
        *,
        color: WireColor = WireColor.RED,
    ) -> None:
        if isinstance(value, RealizedValue):
            self._connect(value.endpoint, target, color=color)

    def _connect(
        self, source: WireEndpoint, target: WireEndpoint, *, color: WireColor = WireColor.RED
    ) -> None:
        if source == target:
            return
        key = (source, target, color)
        reverse = (target, source, color)
        if key in self.connection_keys or reverse in self.connection_keys:
            return
        self.connection_keys.add(key)
        self.circuit.connections.append(WireConnection(source, target, color))

    def _take_id(self) -> int:
        result = self.next_entity_id
        self.next_entity_id += 1
        return result

    def _add_entity(self, entity: object):
        assert isinstance(entity, (ArithmeticCombinator, DeciderCombinator, ConstantCombinator))
        self.circuit.entities.append(entity)
        return entity

    def _count_uses(self) -> Counter[int]:
        counts: Counter[int] = Counter()
        for op in self.module.operations:
            for child in dependencies(op):
                counts[id(child)] += 1
        for output in self.module.output.values:
            counts[id(output)] += 1
        return counts


def lower_naive(
    module: CircuitModule, *, state_timing: StateTimingPlan | None = None
) -> PhysicalCircuit:
    """Lower the semantic DAG without lane packing."""

    return PhysicalLowerer(
        module, enable_packing=False, state_timing=state_timing
    ).lower()


def lower_with_alu_packing(
    module: CircuitModule, *, state_timing: StateTimingPlan | None = None
) -> PhysicalCircuit:
    """Lower with conservative compatibility-group ``Each`` packing."""

    return PhysicalLowerer(
        module, enable_packing=True, state_timing=state_timing
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
