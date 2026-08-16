"""Symbolic-frontend lowering and the canonical Level Flow normalization boundary.

The symbolic frontend intentionally keeps its long-standing compatibility nodes: ``Input`` and
``InputSample`` are useful public records and ``Circuit.step()`` remains the elaboration API. The
compiler never sends those raw records directly to later semantic passes. ``normalize_module``
contextualizes raw Level observations and annotates every ordinary scalar/vector expression with a
Flow. The canonical nodes remain instances of the legacy records where practical, so unchanged
backend consumers retain their field and ``isinstance`` contracts.
"""

from __future__ import annotations

from typing import cast

from factorio_circuit.frontend.symbolic import Circuit
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Clock,
    ClockProvenance,
    Compare,
    Constant,
    Flow,
    FlowInput,
    FlowInputSample,
    FlowVectorInput,
    FlowVectorInputSample,
    FlowVectorRegisterRead,
    Input,
    InputSample,
    PayloadShape,
    ReturnValue,
    ScalarValue,
    Select,
    TemporalModality,
    VectorBinaryOp,
    VectorConstant,
    VectorFilter,
    VectorInput,
    VectorInputSample,
    VectorScalarOp,
    VectorSelect,
    VectorSignal,
    VectorValue,
    contains_event_semantics,
    is_vector_value,
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
    VectorRegisterRead,
    state_transitions,
)


class ClockNormalizationError(ValueError):
    """Raised when one ordinary expression region contains incompatible structural clocks."""


class _Normalizer:
    def __init__(self, module: CircuitModule) -> None:
        self.module = module
        self.default_clock = Clock(
            identity=f"{module.name}:level",
            provenance=ClockProvenance.INFERRED,
        )
        self._scalar_cache: dict[tuple[int, Clock], ScalarValue] = {}
        self._vector_cache: dict[tuple[int, Clock], VectorValue] = {}
        self._register_clocks = self._make_register_clocks()

    def _make_register_clocks(self) -> dict[StateRegister, Clock]:
        registers = tuple(self.module.state_registers)
        parent = {register.name: register.name for register in registers}

        def find(name: str) -> str:
            root = name
            while parent[root] != root:
                root = parent[root]
            while parent[name] != name:
                next_name = parent[name]
                parent[name] = root
                name = next_name
            return root

        def union(left: StateRegister, right: StateRegister) -> None:
            left_root = find(left.name)
            right_root = find(right.name)
            if left_root != right_root:
                parent[right_root] = left_root

        for transition in state_transitions(self.module):
            if transition.trigger is not None:
                continue
            referenced: set[StateRegister] = set()
            if transition.kind in {"add", "set"} and transition.value is not None:
                referenced.update(self._registers_in_value(transition.value))
            if transition.kind in {"add", "clear", "set"} and transition.when is not None:
                referenced.update(self._registers_in_value(transition.when))
            for source in referenced:
                union(transition.register, source)

        # A single ordinary output expression is one evaluation region too. If it observes
        # multiple state registers, give the region one structural clock just as timing analysis
        # does; otherwise normalization would invent an implicit cross-domain crossing.
        for output in self.module.output.values:
            referenced_list = sorted(
                self._registers_in_value(output), key=lambda register: register.name
            )
            if referenced_list:
                first = referenced_list[0]
                for other in referenced_list[1:]:
                    union(first, other)

        groups: dict[str, list[StateRegister]] = {}
        for register in registers:
            groups.setdefault(find(register.name), []).append(register)

        transition_clocks = {
            transition.register: transition.clock
            for transition in state_transitions(self.module)
            if transition.trigger is None and transition.legacy is None
        }
        result: dict[StateRegister, Clock] = {}
        for group in groups.values():
            names = ",".join(register.name for register in group)
            fixed = {
                transition_clocks[register] for register in group if register in transition_clocks
            }
            if len(fixed) > 1:
                raise ClockNormalizationError(
                    "connected state registers carry incompatible canonical transition clocks"
                )
            clock = next(iter(fixed), None) or Clock(
                identity=f"{self.module.name}:state:{names}",
                provenance=ClockProvenance.INFERRED,
            )
            result.update({register: clock for register in group})
        return result

    def _registers_in_value(self, value: object) -> set[StateRegister]:
        seen: set[int] = set()

        def visit(item: object) -> set[StateRegister]:
            if id(item) in seen:
                return set()
            seen.add(id(item))
            if isinstance(item, VectorRegisterRead):
                return {item.register}
            if isinstance(item, (Input, InputSample, Constant, VectorInput, VectorInputSample)):
                return set()
            if isinstance(item, VectorConstant):
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

    def _clock_for_register(self, register: StateRegister) -> Clock:
        try:
            return self._register_clocks[register]
        except KeyError as exc:  # pragma: no cover - malformed external module
            raise ClockNormalizationError(
                f"state register {register.name!r} is not declared by the module"
            ) from exc

    @staticmethod
    def _flow(value: object) -> Flow | None:
        flow = getattr(value, "flow", None)
        return flow if isinstance(flow, Flow) else None

    @staticmethod
    def _key(value: object, clock: Clock) -> tuple[int, Clock]:
        return (id(value), clock)

    @staticmethod
    def _offset(*values: object) -> int:
        offsets = [
            flow.logical_offset
            for value in values
            if not isinstance(value, (Constant, VectorConstant))
            if (flow := _Normalizer._flow(value)) is not None
        ]
        return offsets[0] if offsets and len(set(offsets)) == 1 else 0

    def _make_flow(
        self,
        reference: object,
        shape: PayloadShape,
        clock: Clock,
        offset: int,
    ) -> Flow:
        return Flow(reference, shape, TemporalModality.LEVEL, clock, offset)

    def scalar(self, value: ScalarValue, expected: Clock) -> ScalarValue:
        key = self._key(value, expected)
        cached = self._scalar_cache.get(key)
        if cached is not None:
            return cached

        existing = self._flow(value)

        result: ScalarValue
        if isinstance(value, FlowInput):
            result = (
                value
                if existing is not None and existing.clock == expected
                else FlowInput(
                    name=value.name,
                    source=value.source,
                    flow=self._make_flow(value.source, PayloadShape.SCALAR, expected, 0),
                )
            )
        elif isinstance(value, Input):
            result = FlowInput(
                name=value.name,
                source=value,
                flow=self._make_flow(value, PayloadShape.SCALAR, expected, 0),
            )
        elif isinstance(value, FlowInputSample):
            result = (
                value
                if existing is not None and existing.clock == expected
                else FlowInputSample(
                    source=value.source,
                    offset=value.offset,
                    name=value.name,
                    flow=self._make_flow(value.source, PayloadShape.SCALAR, expected, value.offset),
                )
            )
        elif isinstance(value, InputSample):
            result = FlowInputSample(
                source=value.source,
                offset=value.offset,
                name=value.name,
                flow=self._make_flow(value.source, PayloadShape.SCALAR, expected, value.offset),
            )
        elif isinstance(value, Constant):
            result = (
                value
                if existing is not None and existing.clock == expected
                else Constant(
                    value.value,
                    value.name,
                    self._make_flow(value, PayloadShape.SCALAR, expected, 0),
                )
            )
        elif isinstance(value, BinaryOp):
            left = self.scalar(value.left, expected)
            right = self.scalar(value.right, expected)
            if existing is not None and left is value.left and right is value.right:
                result = value
            else:
                result = BinaryOp(
                    value.op,
                    left,
                    right,
                    value.name,
                    self._make_flow(
                        value, PayloadShape.SCALAR, expected, self._offset(left, right)
                    ),
                )
        elif isinstance(value, Compare):
            left = self.scalar(value.left, expected)
            right = self.scalar(value.right, expected)
            if existing is not None and left is value.left and right is value.right:
                result = value
            else:
                result = Compare(
                    value.op,
                    left,
                    right,
                    value.name,
                    self._make_flow(
                        value, PayloadShape.SCALAR, expected, self._offset(left, right)
                    ),
                )
        elif isinstance(value, Select):
            condition = self.scalar(value.condition, expected)
            when_true = self.scalar(value.when_true, expected)
            when_false = self.scalar(value.when_false, expected)
            if (
                existing is not None
                and condition is value.condition
                and when_true is value.when_true
                and when_false is value.when_false
            ):
                result = value
            else:
                result = Select(
                    condition,
                    when_true,
                    when_false,
                    value.name,
                    self._make_flow(
                        value,
                        PayloadShape.SCALAR,
                        expected,
                        self._offset(condition, when_true, when_false),
                    ),
                )
        elif isinstance(value, VectorSignal):
            vector = self.vector(value.vector, expected)
            if existing is not None and vector is value.vector:
                result = value
            else:
                result = VectorSignal(
                    vector,
                    value.signal,
                    value.name,
                    self._make_flow(value, PayloadShape.SCALAR, expected, self._offset(vector)),
                )
        else:  # pragma: no cover - guarded by ScalarValue
            raise TypeError(value)
        self._scalar_cache[key] = result
        return result

    def vector(self, value: VectorValue, expected: Clock) -> VectorValue:
        key = self._key(value, expected)
        cached = self._vector_cache.get(key)
        if cached is not None:
            return cached

        existing = self._flow(value)

        result: VectorValue
        if isinstance(value, FlowVectorInput):
            result = (
                value
                if existing is not None and existing.clock == expected
                else FlowVectorInput(
                    name=value.name,
                    source=value.source,
                    flow=self._make_flow(value.source, PayloadShape.VECTOR, expected, 0),
                )
            )
        elif isinstance(value, VectorInput):
            result = FlowVectorInput(
                name=value.name,
                source=value,
                flow=self._make_flow(value, PayloadShape.VECTOR, expected, 0),
            )
        elif isinstance(value, FlowVectorInputSample):
            result = (
                value
                if existing is not None and existing.clock == expected
                else FlowVectorInputSample(
                    source=value.source,
                    offset=value.offset,
                    name=value.name,
                    flow=self._make_flow(value.source, PayloadShape.VECTOR, expected, value.offset),
                )
            )
        elif isinstance(value, VectorInputSample):
            result = FlowVectorInputSample(
                source=value.source,
                offset=value.offset,
                name=value.name,
                flow=self._make_flow(value.source, PayloadShape.VECTOR, expected, value.offset),
            )
        elif isinstance(value, VectorConstant):
            result = (
                value
                if existing is not None and existing.clock == expected
                else VectorConstant(
                    value.signals,
                    value.name,
                    self._make_flow(value, PayloadShape.VECTOR, expected, 0),
                )
            )
        elif isinstance(value, FlowVectorRegisterRead):
            register_clock = self._clock_for_register(value.register)
            if (
                value.flow is None
                or value.flow.payload_shape is not PayloadShape.VECTOR
                or value.flow.modality is not TemporalModality.LEVEL
                or value.flow.logical_offset != value.offset
            ):
                raise ClockNormalizationError(
                    "intrinsically clocked state read has invalid Flow metadata"
                )
            if expected != register_clock:
                raise ClockNormalizationError(
                    "intrinsically clocked state read cannot be normalized to another clock"
                )
            if value.flow.clock == register_clock:
                result = value
            elif value.flow.clock.provenance is ClockProvenance.INFERRED:
                result = FlowVectorRegisterRead(
                    register=value.register,
                    offset=value.offset,
                    order=value.order,
                    name=value.name,
                    flow=self._make_flow(value, PayloadShape.VECTOR, register_clock, value.offset),
                )
            else:
                raise ClockNormalizationError(
                    "intrinsically clocked state read cannot cross its structural clock"
                )
        elif isinstance(value, VectorRegisterRead):
            register_clock = self._clock_for_register(value.register)
            if register_clock != expected:
                raise ClockNormalizationError(
                    "incompatible normalized clocks in one Level expression region: "
                    f"state {register_clock.identity!r} and {expected.identity!r}"
                )
            result = FlowVectorRegisterRead(
                register=value.register,
                offset=value.offset,
                order=value.order,
                name=value.name,
                flow=self._make_flow(value, PayloadShape.VECTOR, expected, value.offset),
            )
        elif isinstance(value, VectorBinaryOp):
            left = self.vector(value.left, expected)
            right = self.vector(value.right, expected)
            if existing is not None and left is value.left and right is value.right:
                result = value
            else:
                result = VectorBinaryOp(
                    value.op,
                    left,
                    right,
                    self._make_flow(
                        value, PayloadShape.VECTOR, expected, self._offset(left, right)
                    ),
                )
        elif isinstance(value, VectorScalarOp):
            vector = self.vector(value.vector, expected)
            scalar = self.scalar(value.scalar, expected)
            if existing is not None and vector is value.vector and scalar is value.scalar:
                result = value
            else:
                result = VectorScalarOp(
                    value.op,
                    vector,
                    scalar,
                    self._make_flow(
                        value, PayloadShape.VECTOR, expected, self._offset(vector, scalar)
                    ),
                )
        elif isinstance(value, VectorSelect):
            vector = self.vector(value.vector, expected)
            if existing is not None and vector is value.vector:
                result = value
            else:
                result = VectorSelect(
                    value.op,
                    vector,
                    value.right,
                    flow=self._make_flow(
                        value, PayloadShape.VECTOR, expected, self._offset(vector)
                    ),
                    select_max=value.select_max,
                    index=value.index,
                )
        elif isinstance(value, VectorFilter):
            vector = self.vector(value.vector, expected)
            if existing is not None and vector is value.vector:
                result = value
            else:
                result = VectorFilter(
                    value.op,
                    vector,
                    value.right,
                    self._make_flow(value, PayloadShape.VECTOR, expected, self._offset(vector)),
                )
        else:  # pragma: no cover - guarded by VectorValue
            raise TypeError(value)
        self._vector_cache[key] = result
        return result

    def _root_clock(self, value: object) -> Clock:
        if isinstance(value, VectorRegisterRead):
            return self._clock_for_register(value.register)
        if isinstance(value, VectorSignal):
            return self._root_clock(value.vector)
        if isinstance(value, (BinaryOp, Compare)):
            clocks = [self._root_clock(value.left), self._root_clock(value.right)]
            return next(
                (clock for clock in clocks if clock != self.default_clock), self.default_clock
            )
        if isinstance(value, Select):
            clocks = [
                self._root_clock(value.condition),
                self._root_clock(value.when_true),
                self._root_clock(value.when_false),
            ]
            return next(
                (clock for clock in clocks if clock != self.default_clock), self.default_clock
            )
        if isinstance(value, VectorBinaryOp):
            clocks = [self._root_clock(value.left), self._root_clock(value.right)]
            return next(
                (clock for clock in clocks if clock != self.default_clock), self.default_clock
            )
        if isinstance(value, VectorScalarOp):
            clocks = [self._root_clock(value.vector), self._root_clock(value.scalar)]
            return next(
                (clock for clock in clocks if clock != self.default_clock), self.default_clock
            )
        if isinstance(value, (VectorFilter, VectorSelect)):
            return self._root_clock(value.vector)
        return self.default_clock

    def normalize(self) -> CircuitModule:
        state_operations: list[StateOperation] = []
        for transition in state_transitions(self.module):
            if transition.trigger is not None:
                continue
            clock = self._clock_for_register(transition.register)
            if transition.kind == "add":
                if not isinstance(transition.register, AccumulatorRegister):
                    raise ClockNormalizationError("add transition requires an accumulator register")
                if transition.value is None or transition.when is None:
                    raise ClockNormalizationError(
                        "add transition is missing its value or condition"
                    )
                state_operations.append(
                    AccumulatorAdd(
                        transition.register,
                        self.vector(transition.value, clock),
                        self.scalar(transition.when, clock),
                        transition.order,
                    )
                )
            elif transition.kind == "clear":
                if not isinstance(transition.register, AccumulatorRegister):
                    raise ClockNormalizationError(
                        "clear transition requires an accumulator register"
                    )
                if transition.when is None:
                    raise ClockNormalizationError("clear transition is missing its condition")
                state_operations.append(
                    AccumulatorClear(
                        transition.register,
                        self.scalar(transition.when, clock),
                        transition.order,
                    )
                )
            elif transition.kind == "set":
                if not isinstance(transition.register, FreezeRegister):
                    raise ClockNormalizationError("set transition requires a freeze register")
                if transition.value is None or transition.when is None:
                    raise ClockNormalizationError(
                        "set transition is missing its value or condition"
                    )
                state_operations.append(
                    FreezeSet(
                        transition.register,
                        self.vector(transition.value, clock),
                        self.scalar(transition.when, clock),
                        transition.order,
                    )
                )

        outputs: list[object] = []
        for value in self.module.output.values:
            clock = self._root_clock(value)
            if is_vector_value(value):
                outputs.append(self.vector(cast(VectorValue, value), clock))
            else:
                outputs.append(self.scalar(cast(ScalarValue, value), clock))

        # Normalize declared operations as well. This keeps the canonical module self-contained for
        # diagnostics/debugging even when an operation is not reachable from an output.
        operations = tuple(
            self.scalar(
                operation,
                self._root_clock(operation),
            )
            for operation in self.module.operations
        )
        normalized = CircuitModule(
            name=self.module.name,
            inputs=self.module.inputs,
            operations=operations,  # type: ignore[arg-type]
            output=ReturnValue(tuple(outputs), self.module.output.names),  # type: ignore[arg-type]
            vector_inputs=self.module.vector_inputs,
            state_registers=self.module.state_registers,
            state_operations=tuple(state_operations),
            event_inputs=self.module.event_inputs,
            event_state_operations=self.module.event_state_operations,
            sample_on_crossings=self.module.sample_on_crossings,
            register_clocks=tuple(
                (register, self._register_clocks[register])
                for register in self.module.state_registers
            ),
        )
        return CircuitModule(
            name=normalized.name,
            inputs=normalized.inputs,
            operations=normalized.operations,
            output=normalized.output,
            vector_inputs=normalized.vector_inputs,
            state_registers=normalized.state_registers,
            state_operations=normalized.state_operations,
            event_inputs=normalized.event_inputs,
            event_state_operations=normalized.event_state_operations,
            sample_on_crossings=normalized.sample_on_crossings,
            register_clocks=normalized.register_clocks,
            transitions=state_transitions(normalized),
        )


def normalize_module(module: CircuitModule) -> CircuitModule:
    """Return an idempotently canonical Level semantic module.

    Event-bearing modules already carry explicit clocked-flow metadata and therefore pass through
    this Level contextualization step unchanged.
    """

    state_transitions(module)  # Validate canonical/legacy representation ambiguity first.
    if contains_event_semantics(module):
        return module
    normalized = _Normalizer(module).normalize()
    validate_canonical_module(normalized)
    return normalized


def lower_frontend(source: Circuit | CircuitModule) -> CircuitModule:
    if isinstance(source, CircuitModule):
        return normalize_module(source)
    if isinstance(source, Circuit):
        return normalize_module(source.build())
    raise TypeError(
        "compile_circuit() expects a symbolic Circuit; decorated Python functions are no longer "
        "the circuit frontend"
    )
