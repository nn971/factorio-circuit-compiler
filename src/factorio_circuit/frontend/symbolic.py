"""Symbolic Python frontend for constructing Factorio circuit modules.

Python executes this module normally as elaboration code.  ``Expr`` objects represent logical signal
streams; overloaded operators construct immutable semantic-IR nodes rather than performing circuit
work at Python runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

from factorio_circuit.ir.semantic import (
    BinaryOp,
    CircuitModule,
    Compare,
    Constant,
    DerivedValue,
    Input as IRInput,
    InputSample,
    OutputValue,
    ReturnValue,
    ScalarValue,
    Select,
    VectorInput as IRVectorInput,
    VectorInputSample,
    VectorConstant,
    VectorSignal,
    VectorValue,
)
from factorio_circuit.ir.physical import SignalId
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

    # Python expects __eq__/__ne__ to be usable for arbitrary objects; for this DSL they intentionally
    # construct logical comparisons just like tensor libraries do.
    def __eq__(self, other: object) -> Expr:  # type: ignore[override]
        if not isinstance(other, (Expr, int, bool)):
            return NotImplemented  # type: ignore[return-value]
        return self._compare("==", other)

    def __ne__(self, other: object) -> Expr:  # type: ignore[override]
        if not isinstance(other, (Expr, int, bool)):
            return NotImplemented  # type: ignore[return-value]
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

    def sample(self) -> SignalsExpr:
        offset = self._circuit.now.offset
        if offset == 0:
            return self
        return SignalsExpr(
            self._circuit, self._circuit._sample_vector_input(self._source, offset)
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
        self._outputs: list[OutputValue] = []
        self._output_names: list[str | None] = []
        self._freshness = 0
        self._state_order: dict[str, int] = {}
        self._used_names: set[str] = set()
        self._state_counter = 0
        self._scalar_samples: dict[tuple[int, int], InputSample] = {}
        self._vector_samples: dict[tuple[int, int], VectorInputSample] = {}

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
        if isinstance(value, (int, bool)):
            value = self._coerce_scalar(value)
        else:
            self._require_owned(value)
        if name in {item for item in self._output_names if item is not None}:
            raise CircuitBuildError(f"output {name!r} is already declared")
        self._outputs.append(value._value)
        self._output_names.append(name)

    def build(self) -> CircuitModule:
        """Freeze the currently elaborated graph into semantic IR."""

        if not self._outputs:
            raise CircuitBuildError("circuit has no outputs")
        return CircuitModule(
            name=self.name,
            inputs=tuple(self._inputs),
            operations=tuple(self._operations),
            output=ReturnValue(tuple(self._outputs), tuple(self._output_names)),
            vector_inputs=tuple(self._vector_inputs),
            state_registers=tuple(self._state_registers),
            state_operations=tuple(self._state_operations),
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
            DEFAULT_SAFE_WIRE_SPAN
            if blueprint_safe_wire_span is None
            else blueprint_safe_wire_span
        )
        return compile_circuit(
            self, optimize=optimize, blueprint_safe_wire_span=safe_span
        )

    def _derived(self, value: DerivedValue) -> Expr:
        self._operations.append(value)
        return Expr(self, value)

    def _sample_scalar_input(self, source: IRInput, offset: int) -> InputSample:
        key = (id(source), offset)
        return self._scalar_samples.setdefault(key, InputSample(source, offset))

    def _sample_vector_input(
        self, source: IRVectorInput, offset: int
    ) -> VectorInputSample:
        key = (id(source), offset)
        return self._vector_samples.setdefault(key, VectorInputSample(source, offset))

    def _coerce_scalar(self, value: ScalarLike) -> Expr:
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

    def _next_state_order(self, register: StateRegister) -> int:
        result = self._state_order[register.name]
        self._state_order[register.name] = result + 1
        return result

    def _append_state_operation(self, operation: StateOperation) -> None:
        self._state_operations.append(operation)


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
