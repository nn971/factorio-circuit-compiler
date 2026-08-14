"""Symbolic Python frontend for constructing Factorio circuit modules.

Python executes this module normally as elaboration code.  ``Expr`` objects represent logical signal
streams; overloaded operators construct immutable semantic-IR nodes rather than performing circuit
work at Python runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, cast

from factorio_circuit.events import (
    EventCausalityError,
    EventCrossingError,
    EventMaterializationError,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Clock,
    ClockProvenance,
    Compare,
    Constant,
    DerivedValue,
    EventInput,
    Flow,
    InputSample,
    OutputValue,
    PayloadShape,
    ReturnValue,
    SampleOn,
    ScalarValue,
    Select,
    TemporalModality,
    VectorConstant,
    VectorInput,
    VectorInputSample,
    VectorSignal,
    VectorValue,
)
from factorio_circuit.ir.semantic import (
    Input as IRInput,
)
from factorio_circuit.ir.semantic import (
    Input as IRScalarInput,
)
from factorio_circuit.ir.semantic import (
    VectorInput as IRVectorInput,
)
from factorio_circuit.ir.state import (
    AccumulatorAdd,
    AccumulatorClear,
    AccumulatorRegister,
    FreezeCapture,
    FreezeRegister,
    FreezeSet,
    StateOperation,
    StateRegister,
    VectorRegisterRead,
)

if TYPE_CHECKING:
    from factorio_circuit.compiler import CompilationResult


class CircuitBuildError(ValueError):
    """Raised when symbolic objects are combined in an invalid circuit construction."""


@dataclass(frozen=True, slots=True)
class LogicalTime:
    """A semantic offset from the current invocation's base tick."""

    offset: int


class Expr:
    """A scalar logical stream expression.

    The expression has semantic sample provenance but no user-visible physical execution tick.
    """

    __slots__ = ("_circuit", "_value")

    def __init__(self, circuit: Circuit, value: ScalarValue) -> None:
        self._circuit = circuit
        self._value = value

    @property
    def circuit(self) -> Circuit:
        return self._circuit

    @property
    def ir(self) -> ScalarValue:
        """Return the underlying semantic value for compiler/debugging code."""

        return self._value

    def select(self, when_true: ScalarLike, when_false: ScalarLike) -> Expr:
        """Construct a runtime mux using this expression as a nonzero condition."""

        true_value = self._circuit._coerce_scalar(when_true)
        false_value = self._circuit._coerce_scalar(when_false)
        condition = self
        if not isinstance(self._value, Compare):
            condition = self._circuit._derived(Compare("!=", self._value, Constant(0)))
        return self._circuit._derived(
            Select(
                condition=condition._value,
                when_true=true_value._value,
                when_false=false_value._value,
            )
        )

    def __bool__(self) -> NoReturn:
        raise CircuitBuildError(
            "symbolic Expr values cannot drive Python if/while; use condition.select(true, false) "
            "for circuit-time branching"
        )

    def _binary(self, op: str, other: ScalarLike) -> Expr:
        right = self._circuit._coerce_scalar(other)
        return self._circuit._derived(BinaryOp(op, self._value, right._value))

    def _rbinary(self, op: str, other: ScalarLike) -> Expr:
        left = self._circuit._coerce_scalar(other)
        return self._circuit._derived(BinaryOp(op, left._value, self._value))

    def _compare(self, op: str, other: ScalarLike) -> Expr:
        right = self._circuit._coerce_scalar(other)
        return self._circuit._derived(Compare(op, self._value, right._value))

    def __add__(self, other: ScalarLike) -> Expr:
        return self._binary("+", other)

    def __radd__(self, other: ScalarLike) -> Expr:
        return self._rbinary("+", other)

    def __sub__(self, other: ScalarLike) -> Expr:
        return self._binary("-", other)

    def __rsub__(self, other: ScalarLike) -> Expr:
        return self._rbinary("-", other)

    def __mul__(self, other: ScalarLike) -> Expr:
        return self._binary("*", other)

    def __rmul__(self, other: ScalarLike) -> Expr:
        return self._rbinary("*", other)

    def __floordiv__(self, other: ScalarLike) -> Expr:
        return self._binary("//", other)

    def __rfloordiv__(self, other: ScalarLike) -> Expr:
        return self._rbinary("//", other)

    def __truediv__(self, other: ScalarLike) -> Expr:
        return self._binary("/", other)

    def __rtruediv__(self, other: ScalarLike) -> Expr:
        return self._rbinary("/", other)

    def __mod__(self, other: ScalarLike) -> Expr:
        return self._binary("%", other)

    def __rmod__(self, other: ScalarLike) -> Expr:
        return self._rbinary("%", other)

    def __lshift__(self, other: ScalarLike) -> Expr:
        return self._binary("<<", other)

    def __rlshift__(self, other: ScalarLike) -> Expr:
        return self._rbinary("<<", other)

    def __rshift__(self, other: ScalarLike) -> Expr:
        return self._binary(">>", other)

    def __rrshift__(self, other: ScalarLike) -> Expr:
        return self._rbinary(">>", other)

    def __and__(self, other: ScalarLike) -> Expr:
        return self._binary("&", other)

    def __rand__(self, other: ScalarLike) -> Expr:
        return self._rbinary("&", other)

    def __or__(self, other: ScalarLike) -> Expr:
        return self._binary("|", other)

    def __ror__(self, other: ScalarLike) -> Expr:
        return self._rbinary("|", other)

    def __xor__(self, other: ScalarLike) -> Expr:
        return self._binary("^", other)

    def __rxor__(self, other: ScalarLike) -> Expr:
        return self._rbinary("^", other)

    def __pow__(self, other: ScalarLike) -> Expr:
        return self._binary("**", other)

    def __rpow__(self, other: ScalarLike) -> Expr:
        return self._rbinary("**", other)

    def __neg__(self) -> Expr:
        return self * -1

    def logical_not(self) -> Expr:
        """Return 1 when this expression is zero and 0 otherwise."""

        return self._compare("==", 0)

    def __lt__(self, other: ScalarLike) -> Expr:
        return self._compare("<", other)

    def __le__(self, other: ScalarLike) -> Expr:
        return self._compare("<=", other)

    def __gt__(self, other: ScalarLike) -> Expr:
        return self._compare(">", other)

    def __ge__(self, other: ScalarLike) -> Expr:
        return self._compare(">=", other)

    # Python expects __eq__/__ne__ to be usable for arbitrary objects. This DSL intentionally
    # construct logical comparisons just like tensor libraries do.
    def __eq__(self, other: object) -> Expr:  # type: ignore[override]
        if not isinstance(other, (Expr, int, bool)):
            return NotImplemented
        return self._compare("==", other)

    def __ne__(self, other: object) -> Expr:  # type: ignore[override]
        if not isinstance(other, (Expr, int, bool)):
            return NotImplemented
        return self._compare("!=", other)


class Input(Expr):
    """A scalar external stream that can be sampled at the circuit freshness cursor."""

    __slots__ = ("_source",)

    def __init__(self, circuit: Circuit, source: IRInput) -> None:
        super().__init__(circuit, source)
        self._source = source

    @property
    def name(self) -> str:
        return self._source.name

    @property
    def flow(self) -> Flow:
        """Return the Level-flow metadata attached to this legacy source."""

        return self._circuit._input_flow(self._source, PayloadShape.SCALAR)

    def sample(self) -> Expr:
        """Observe this external source at the circuit's current logical time."""

        offset = self._circuit.now.offset
        if offset == 0:
            return self
        return Expr(self._circuit, self._circuit._sample_scalar_input(self._source, offset))


class SignalsExpr:
    """A symbolic whole Factorio signal-vector stream."""

    __slots__ = ("_circuit", "_value")

    def __init__(self, circuit: Circuit, value: VectorValue) -> None:
        self._circuit = circuit
        self._value = value

    @property
    def circuit(self) -> Circuit:
        return self._circuit

    @property
    def ir(self) -> VectorValue:
        return self._value

    def signal(self, signal: SignalId) -> Expr:
        """Read one concrete signal lane from this vector stream."""

        if not isinstance(signal, SignalId):
            raise CircuitBuildError("SignalsExpr.signal(...) requires a SignalId")
        return Expr(self._circuit, VectorSignal(self._value, signal))


class SignalsInput(SignalsExpr):
    """A whole-vector external source that can be sampled at the freshness cursor."""

    __slots__ = ("_source",)

    def __init__(self, circuit: Circuit, source: IRVectorInput) -> None:
        super().__init__(circuit, source)
        self._source = source

    @property
    def name(self) -> str:
        return self._source.name

    @property
    def flow(self) -> Flow:
        """Return the Level-flow metadata attached to this legacy source."""

        return self._circuit._input_flow(self._source, PayloadShape.VECTOR)

    def sample(self) -> SignalsExpr:
        offset = self._circuit.now.offset
        if offset == 0:
            return self
        return SignalsExpr(self._circuit, self._circuit._sample_vector_input(self._source, offset))


class ScalarEvent:
    """A scalar Event source handle, intentionally outside the expression DSL."""

    __slots__ = ("_circuit", "_source")

    def __init__(self, circuit: Circuit, source: EventInput) -> None:
        self._circuit = circuit
        self._source = source

    @property
    def name(self) -> str:
        return self._source.name

    @property
    def ir(self) -> EventInput:
        return self._source

    @property
    def clock(self) -> Clock:
        return self._source.clock

    @property
    def flow(self) -> Flow:
        return self._circuit._event_flow(self._source, PayloadShape.SCALAR)


class VectorEvent:
    """A vector Event source handle, intentionally outside the expression DSL."""

    __slots__ = ("_circuit", "_source")

    def __init__(self, circuit: Circuit, source: EventInput) -> None:
        self._circuit = circuit
        self._source = source

    @property
    def name(self) -> str:
        return self._source.name

    @property
    def ir(self) -> EventInput:
        return self._source

    @property
    def clock(self) -> Clock:
        return self._source.clock

    @property
    def flow(self) -> Flow:
        return self._circuit._event_flow(self._source, PayloadShape.VECTOR)


class SampleOnReference:
    """A semantic-only reference to a same-circuit Level-to-Event crossing."""

    __slots__ = ("_crossing",)

    def __init__(self, crossing: SampleOn) -> None:
        self._crossing = crossing

    @property
    def ir(self) -> SampleOn:
        return self._crossing

    @property
    def source(self) -> IRInput | IRVectorInput:
        return self._crossing.source

    @property
    def target(self) -> EventInput:
        return self._crossing.target

    @property
    def clock(self) -> Clock:
        return self._crossing.target.clock

    @property
    def payload_shape(self) -> PayloadShape:
        return (
            PayloadShape.SCALAR
            if isinstance(self._crossing.source, IRInput)
            else PayloadShape.VECTOR
        )


ScalarLike = Expr | int | bool
OutputExpr = Expr | SignalsExpr
OutputLike = OutputExpr | int | bool


class Circuit:
    """Mutable Python elaboration context that builds one immutable circuit module."""

    def __init__(self, name: str) -> None:
        if not name:
            raise CircuitBuildError("circuit name must be non-empty")
        self.name = name
        self._inputs: list[IRInput] = []
        self._vector_inputs: list[IRVectorInput] = []
        self._operations: list[DerivedValue] = []
        self._state_registers: list[StateRegister] = []
        self._state_operations: list[StateOperation] = []
        self._event_state_operations: list[FreezeCapture] = []
        self._outputs: list[OutputValue] = []
        self._output_names: list[str | None] = []
        self._freshness = 0
        self._state_order: dict[str, int] = {}
        self._used_names: set[str] = set()
        self._state_counter = 0
        self._scalar_samples: dict[tuple[int, int], InputSample] = {}
        self._vector_samples: dict[tuple[int, int], VectorInputSample] = {}
        self._input_flows: dict[tuple[str, PayloadShape], Flow] = {}
        self._event_inputs: list[EventInput] = []
        self._event_flows: dict[tuple[str, PayloadShape], Flow] = {}
        self._event_capture_registers: set[StateRegister] = set()
        self._sample_on_crossings: list[SampleOn] = []
        self._sample_on_index: dict[SampleOn, SampleOn] = {}

    @property
    def now(self) -> LogicalTime:
        return LogicalTime(self._freshness)

    def input(self, name: str) -> Input:
        self._claim_name(name, "input")
        value = IRInput(name)
        self._inputs.append(value)
        return Input(self, value)

    def signals(self, name: str) -> SignalsInput:
        """Declare a whole circuit-network signal-vector input."""

        self._claim_name(name, "input")
        value = IRVectorInput(name)
        self._vector_inputs.append(value)
        return SignalsInput(self, value)

    def event(self, name: str, *, guaranteed_min_separation: int) -> ScalarEvent:
        """Declare a scalar external Event source for semantic reference simulation."""

        return cast(
            ScalarEvent,
            self._declare_event(name, PayloadShape.SCALAR, guaranteed_min_separation, ScalarEvent),
        )

    def signal_event(self, name: str, *, guaranteed_min_separation: int) -> VectorEvent:
        """Declare a vector external Event source for semantic reference simulation."""

        return cast(
            VectorEvent,
            self._declare_event(name, PayloadShape.VECTOR, guaranteed_min_separation, VectorEvent),
        )

    def _declare_event(
        self,
        name: str,
        payload_shape: PayloadShape,
        guaranteed_min_separation: int,
        handle_type: type[ScalarEvent] | type[VectorEvent],
    ) -> ScalarEvent | VectorEvent:
        self._claim_name(name, "event input")
        clock = Clock(
            identity=f"{self.name}:{name}:{payload_shape.value}:event",
            provenance=ClockProvenance.EXTERNAL_EVENT,
            guaranteed_min_separation=guaranteed_min_separation,
        )
        source = EventInput(name, payload_shape, clock)
        self._event_inputs.append(source)
        return handle_type(self, source)

    def sample_on(
        self,
        source: Input | SignalsInput,
        target: ScalarEvent | VectorEvent,
    ) -> SampleOnReference:
        """Declare or retrieve a semantic Level value sampled on an Event occurrence."""

        from factorio_circuit.frontend.vector_circuit import SignalsInput as RuntimeSignalsInput

        if isinstance(source, (ScalarEvent, VectorEvent)):
            raise EventCrossingError(
                "Event sources cannot be SampleOn sources; deferred stateful SumInto/HoldInto "
                "semantics are not implemented"
            )
        if not isinstance(source, (Input, SignalsInput, RuntimeSignalsInput)):
            raise EventCrossingError(
                "SampleOn source must be a raw Input or raw SignalsInput; derived values, "
                "samples, state, and constants are not allowed"
            )
        if not isinstance(target, (ScalarEvent, VectorEvent)):
            raise EventCrossingError("SampleOn target must be a declared Event")
        if source._circuit is not self or target._circuit is not self:
            raise EventCrossingError("SampleOn source and target must belong to the same Circuit")
        source_ir = source.ir
        target_ir = target.ir
        if not isinstance(source_ir, (IRInput, IRVectorInput)):
            raise EventCrossingError("SampleOn source must be a raw Level input")
        if isinstance(source_ir, IRInput) and source_ir not in self._inputs:
            raise EventCrossingError("SampleOn source must be a declared Level input")
        if isinstance(source_ir, IRVectorInput) and source_ir not in self._vector_inputs:
            raise EventCrossingError("SampleOn source must be a declared Level input")
        if target_ir not in self._event_inputs:
            raise EventCrossingError("SampleOn target must be a declared Event")
        crossing = SampleOn(source_ir, target_ir)
        existing = self._sample_on_index.get(crossing)
        if existing is None:
            self._sample_on_index[crossing] = crossing
            self._sample_on_crossings.append(crossing)
            existing = crossing
        return SampleOnReference(existing)

    def _input_flow(
        self,
        source: IRInput | IRVectorInput,
        payload_shape: PayloadShape,
    ) -> Flow:
        key = (source.name, payload_shape)
        cached = self._input_flows.get(key)
        if cached is not None:
            return cached
        flow = Flow(
            reference=source,
            payload_shape=payload_shape,
            modality=TemporalModality.LEVEL,
            clock=Clock(
                identity=f"{self.name}:{source.name}:{payload_shape.value}",
                provenance=ClockProvenance.INFERRED,
            ),
        )
        self._input_flows[key] = flow
        return flow

    def _event_flow(self, source: EventInput, payload_shape: PayloadShape) -> Flow:
        key = (source.name, payload_shape)
        cached = self._event_flows.get(key)
        if cached is not None:
            return cached
        flow = Flow(
            reference=source,
            payload_shape=payload_shape,
            modality=TemporalModality.EVENT,
            clock=source.clock,
        )
        self._event_flows[key] = flow
        return flow

    def constant_signals(self, signals: dict[SignalId, int]) -> SignalsExpr:
        """Construct a constant whole-vector stream."""

        normalized: list[tuple[SignalId, int]] = []
        for signal, value in signals.items():
            if not isinstance(signal, SignalId):
                raise CircuitBuildError("constant_signals keys must be SignalId values")
            if isinstance(value, bool) or not isinstance(value, int):
                raise CircuitBuildError("constant_signals values must be integers")
            if value != 0:
                normalized.append((signal, value))
        normalized.sort(key=lambda item: (item[0].kind, item[0].name))
        return SignalsExpr(self, VectorConstant(tuple(normalized)))

    def accumulator(self, name: str | None = None) -> AccumulatorReg:
        return AccumulatorReg(self, name=name)

    def freeze(self, name: str | None = None) -> FreezeReg:
        return FreezeReg(self, name=name)

    def tick(self, n: int = 1) -> None:
        """Advance the freshness cursor without delaying previously constructed expressions."""

        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise CircuitBuildError("tick(n) requires a non-negative integer")
        self._freshness += n

    def tick_until(self, n: int) -> None:
        """Advance the freshness cursor to absolute offset ``n``."""

        if isinstance(n, bool) or not isinstance(n, int) or n < self._freshness:
            raise CircuitBuildError(
                f"tick_until(n) requires an integer n >= current freshness {self._freshness}"
            )
        self._freshness = n

    def output(self, name: str, value: OutputLike) -> None:
        if not name:
            raise CircuitBuildError("output name must be non-empty")
        if isinstance(value, SampleOnReference):
            raise EventCrossingError("SampleOn references cannot be circuit outputs")
        from factorio_circuit.simulate.events import MaterializedEventTrace

        if isinstance(value, MaterializedEventTrace):
            raise EventMaterializationError("materialized Event traces cannot be circuit outputs")
        if isinstance(value, (int, bool)):
            value = self._coerce_scalar(value)
        else:
            if not isinstance(value, (Expr, SignalsExpr)):
                raise CircuitBuildError("outputs require an Expr or SignalsExpr value")
            self._require_owned(value)
        if name in {item for item in self._output_names if item is not None}:
            raise CircuitBuildError(f"output {name!r} is already declared")
        self._outputs.append(value._value)
        self._output_names.append(name)

    def build(self) -> CircuitModule:
        """Freeze the currently elaborated graph into semantic IR."""

        if (
            not self._outputs
            and not self._event_inputs
            and not self._event_state_operations
            and not self._sample_on_crossings
        ):
            raise CircuitBuildError("circuit has no outputs")
        return CircuitModule(
            name=self.name,
            inputs=tuple(self._inputs),
            operations=tuple(self._operations),
            output=ReturnValue(tuple(self._outputs), tuple(self._output_names)),
            vector_inputs=tuple(self._vector_inputs),
            state_registers=tuple(self._state_registers),
            state_operations=tuple(self._state_operations),
            event_inputs=tuple(self._event_inputs),
            event_state_operations=tuple(self._event_state_operations),
            sample_on_crossings=tuple(self._sample_on_crossings),
        )

    def compile(
        self,
        *,
        optimize: bool = True,
        blueprint_safe_wire_span: float | None = None,
    ) -> CompilationResult:
        """Compile this circuit using the public compiler pipeline."""

        from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
        from factorio_circuit.compiler import compile_circuit

        safe_span = (
            DEFAULT_SAFE_WIRE_SPAN if blueprint_safe_wire_span is None else blueprint_safe_wire_span
        )
        return compile_circuit(self, optimize=optimize, blueprint_safe_wire_span=safe_span)

    def _derived(self, value: DerivedValue) -> Expr:
        self._operations.append(value)
        return Expr(self, value)

    def _sample_scalar_input(self, source: IRInput, offset: int) -> InputSample:
        key = (id(source), offset)
        return self._scalar_samples.setdefault(key, InputSample(source, offset))

    def _sample_vector_input(self, source: IRVectorInput, offset: int) -> VectorInputSample:
        key = (id(source), offset)
        return self._vector_samples.setdefault(key, VectorInputSample(source, offset))

    def _coerce_scalar(self, value: ScalarLike) -> Expr:
        if isinstance(value, SampleOnReference):
            raise EventCrossingError("SampleOn references cannot be used in scalar expressions")
        if isinstance(value, Expr):
            self._require_owned(value)
            return value
        if isinstance(value, bool):
            return Expr(self, Constant(int(value)))
        if isinstance(value, int):
            return Expr(self, Constant(value))
        raise CircuitBuildError(f"expected scalar Expr or int, got {type(value).__name__}")

    def _require_owned(self, value: OutputExpr) -> None:
        if value.circuit is not self:
            raise CircuitBuildError("cannot combine symbolic values from different Circuit objects")

    def _claim_name(self, name: str, kind: str) -> None:
        if not name:
            raise CircuitBuildError(f"{kind} name must be non-empty")
        if name in self._used_names:
            raise CircuitBuildError(f"name {name!r} is already used by this circuit")
        self._used_names.add(name)

    def _allocate_state_name(self, prefix: str, requested: str | None) -> str:
        if requested is not None:
            self._claim_name(requested, "state")
            return requested
        while True:
            candidate = f"{prefix}{self._state_counter}"
            self._state_counter += 1
            if candidate not in self._used_names:
                self._used_names.add(candidate)
                return candidate

    def _register_state(self, register: StateRegister) -> None:
        self._state_registers.append(register)
        self._state_order[register.name] = 0

    def _ensure_periodic_freeze_allowed(self, register: FreezeRegister) -> None:
        if register in self._event_capture_registers:
            raise EventCausalityError(
                f"FreezeReg {register.name!r} cannot mix periodic set with Event capture"
            )

    def _capture_value(self, value: SignalsExpr) -> VectorValue:
        self._require_owned(value)
        candidate = value._value
        self._validate_capture_vector(candidate)
        return candidate

    def _validate_capture_scalar(self, value: ScalarValue) -> None:
        if isinstance(value, InputSample):
            if value.offset != 0:
                raise EventCausalityError(
                    "Event capture values require zero-offset Level/state inputs"
                )
            return
        if isinstance(value, (IRScalarInput, Constant)):
            return
        if isinstance(value, VectorSignal):
            self._validate_capture_vector(value.vector)
            return
        if isinstance(value, (BinaryOp, Compare)):
            self._validate_capture_scalar(value.left)
            self._validate_capture_scalar(value.right)
            return
        if isinstance(value, Select):
            self._validate_capture_scalar(value.condition)
            self._validate_capture_scalar(value.when_true)
            self._validate_capture_scalar(value.when_false)
            return
        raise EventCausalityError("unsupported Event capture scalar expression")

    def _validate_capture_vector(self, value: VectorValue) -> None:
        from factorio_circuit.frontend import (
            _VectorBinaryOp,
            _VectorFilter,
            _VectorScalarOp,
            _VectorSelect,
        )

        if isinstance(value, VectorInput):
            return
        if isinstance(value, VectorInputSample):
            if value.offset != 0:
                raise EventCausalityError(
                    "Event capture values require zero-offset Level/state inputs"
                )
            return
        if isinstance(value, VectorRegisterRead):
            if value.offset != 0:
                raise EventCausalityError(
                    "Event capture values require zero-offset Level/state inputs"
                )
            return
        if isinstance(value, VectorConstant):
            return
        if isinstance(value, _VectorBinaryOp):
            self._validate_capture_vector(value.left)
            self._validate_capture_vector(value.right)
            return
        if isinstance(value, _VectorScalarOp):
            self._validate_capture_vector(value.vector)
            self._validate_capture_scalar(value.scalar)
            return
        if isinstance(value, (_VectorSelect, _VectorFilter)):
            self._validate_capture_vector(value.vector)
            return
        raise EventCausalityError("unsupported Event capture vector expression")

    def _next_state_order(self, register: StateRegister) -> int:
        result = self._state_order[register.name]
        self._state_order[register.name] = result + 1
        return result

    def _append_state_operation(self, operation: StateOperation) -> None:
        self._state_operations.append(operation)

    def _append_event_state_operation(self, operation: FreezeCapture) -> None:
        self._event_state_operations.append(operation)
        self._event_capture_registers.add(operation.register)


class AccumulatorReg:
    """Whole-vector additive state object with strict elaboration-order accesses."""

    __slots__ = ("_circuit", "_register")

    def __init__(self, circuit: Circuit, *, name: str | None = None) -> None:
        self._circuit = circuit
        state_name = circuit._allocate_state_name("acc", name)
        self._register = AccumulatorRegister(state_name)
        circuit._register_state(self._register)

    @property
    def value(self) -> SignalsExpr:
        read = VectorRegisterRead(
            register=self._register,
            offset=self._circuit.now.offset,
            order=self._circuit._next_state_order(self._register),
            name=self._register.name,
        )
        return SignalsExpr(self._circuit, read)

    def add(self, value: SignalsExpr, *, when: ScalarLike = 1) -> None:
        self._circuit._require_owned(value)
        condition = self._circuit._coerce_scalar(when)
        self._circuit._append_state_operation(
            AccumulatorAdd(
                register=self._register,
                value=value._value,
                when=condition._value,
                order=self._circuit._next_state_order(self._register),
            )
        )

    def clear(self, when: ScalarLike = 1) -> None:
        condition = self._circuit._coerce_scalar(when)
        self._circuit._append_state_operation(
            AccumulatorClear(
                register=self._register,
                when=condition._value,
                order=self._circuit._next_state_order(self._register),
            )
        )


class FreezeReg:
    """Whole-vector pass/freeze state object with strict elaboration-order accesses."""

    __slots__ = ("_circuit", "_register")

    def __init__(self, circuit: Circuit, *, name: str | None = None) -> None:
        self._circuit = circuit
        state_name = circuit._allocate_state_name("freeze", name)
        self._register = FreezeRegister(state_name)
        circuit._register_state(self._register)

    @property
    def value(self) -> SignalsExpr:
        read = VectorRegisterRead(
            register=self._register,
            offset=self._circuit.now.offset,
            order=self._circuit._next_state_order(self._register),
            name=self._register.name,
        )
        return SignalsExpr(self._circuit, read)

    def set(self, value: SignalsExpr, *, when: ScalarLike) -> None:
        self._circuit._ensure_periodic_freeze_allowed(self._register)
        self._circuit._require_owned(value)
        condition = self._circuit._coerce_scalar(when)
        self._circuit._append_state_operation(
            FreezeSet(
                register=self._register,
                value=value._value,
                when=condition._value,
                order=self._circuit._next_state_order(self._register),
            )
        )

    def capture_on(
        self,
        trigger: ScalarEvent | VectorEvent,
        value: SignalsExpr | None = None,
        *,
        required_min_separation: int,
    ) -> None:
        """Capture a vector value on an Event occurrence for reference simulation."""

        if isinstance(trigger, SampleOnReference):
            raise EventCrossingError("SampleOn references cannot be Event capture triggers")
        if not isinstance(trigger, (ScalarEvent, VectorEvent)):
            raise EventCausalityError("FreezeReg.capture_on requires an Event trigger")
        if trigger._circuit is not self._circuit:
            raise EventCausalityError("cannot capture an Event from a different Circuit")
        if self._register in self._circuit._event_capture_registers:
            raise EventCausalityError(
                f"FreezeReg {self._register.name!r} has multiple Event captures"
            )
        if any(
            isinstance(operation, FreezeSet) and operation.register == self._register
            for operation in self._circuit._state_operations
        ):
            raise EventCausalityError(
                f"FreezeReg {self._register.name!r} cannot mix periodic set with Event capture"
            )
        if (
            isinstance(required_min_separation, bool)
            or not isinstance(required_min_separation, int)
            or required_min_separation < 1
        ):
            raise EventCausalityError("Event capture minimum separation must be positive")
        if isinstance(trigger, ScalarEvent) and value is None:
            raise EventCausalityError("scalar Event capture requires an explicit vector value")
        if isinstance(value, SampleOnReference):
            raise EventCrossingError("SampleOn references cannot be captured as Event values")
        if value is not None and not isinstance(value, SignalsExpr):
            raise EventCausalityError("Event capture value must be a whole-vector expression")
        capture_value = None if value is None else self._circuit._capture_value(value)
        self._circuit._append_event_state_operation(
            FreezeCapture(
                register=self._register,
                trigger=trigger.ir,
                value=capture_value,
                required_min_separation=required_min_separation,
                order=self._circuit._next_state_order(self._register),
            )
        )
