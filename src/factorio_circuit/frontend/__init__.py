"""Public symbolic frontend, including runtime-open vector expressions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    Compare,
    Constant,
    ScalarValue,
    VectorConstant,
    VectorInput,
    VectorSignal,
    VectorValue,
)

from .symbolic import (
    AccumulatorReg,
    CircuitBuildError,
    Expr,
    FreezeReg,
    Input,
    LogicalTime,
)
from .symbolic import Circuit as _Circuit
from .symbolic import SignalsExpr as _SignalsExpr
from .symbolic import SignalsInput as _BaseSignalsInput


@dataclass(frozen=True, slots=True)
class _VectorBinaryOp:
    op: str
    left: VectorValue
    right: VectorValue


@dataclass(frozen=True, slots=True)
class _VectorScalarOp:
    op: str
    vector: VectorValue
    scalar: ScalarValue


@dataclass(frozen=True, slots=True)
class _VectorFilter:
    op: str
    vector: VectorValue
    right: int


@dataclass(frozen=True, slots=True)
class _VectorSelect:
    vector: VectorValue
    select_max: bool
    index: int


_VectorNode = _VectorBinaryOp | _VectorScalarOp | _VectorFilter | _VectorSelect


class SignalsExpr(_SignalsExpr):
    """A whole Factorio signal vector with lane-wise runtime-open operations."""

    def _wrap_vector(self, value: _VectorNode) -> SignalsExpr:
        return SignalsExpr(self._circuit, cast(VectorValue, value))

    def _coerce_vector(self, other: object) -> VectorValue:
        if not isinstance(other, _SignalsExpr):
            raise CircuitBuildError(
                f"expected whole-vector SignalsExpr, got {type(other).__name__}"
            )
        self._circuit._require_owned(other)
        return other.ir

    def __add__(self, other: object) -> SignalsExpr:
        return self._wrap_vector(_VectorBinaryOp("+", self._value, self._coerce_vector(other)))

    def __sub__(self, other: object) -> SignalsExpr:
        return self._wrap_vector(_VectorBinaryOp("-", self._value, self._coerce_vector(other)))

    def __mul__(self, other: Expr | int | bool) -> SignalsExpr:
        scalar = self._circuit._coerce_scalar(other)
        return self._wrap_vector(_VectorScalarOp("*", self._value, scalar.ir))

    def __rmul__(self, other: Expr | int | bool) -> SignalsExpr:
        return self * other

    def __neg__(self) -> SignalsExpr:
        return self * -1

    def positive(self) -> SignalsExpr:
        """Preserve every lane whose count is positive, including its original count."""

        return self._wrap_vector(_VectorFilter(">", self._value, 0))

    def max(self) -> SignalsExpr:
        """Select the nonzero lane with the greatest count, preserving that count."""

        return self._wrap_vector(_VectorSelect(self._value, select_max=True, index=0))

    def any(self) -> Expr:
        """Return 1 exactly when at least one vector lane is nonzero."""

        anything = VectorSignal(
            self._value,
            SignalId("virtual", "signal-anything"),
        )
        return self._circuit._derived(Compare("!=", anything, Constant(0)))

    def gate(self, condition: Expr | int | bool) -> SignalsExpr:
        """Pass the vector when ``condition`` is nonzero, otherwise produce the empty vector."""

        scalar = self._circuit._coerce_scalar(condition)
        active = scalar
        if not isinstance(scalar.ir, Compare):
            active = self._circuit._derived(Compare("!=", scalar.ir, Constant(0)))
        return self * active


class SignalsInput(SignalsExpr):
    """A whole-vector external source that can be sampled at the freshness cursor."""

    __slots__ = ("_source",)

    def __init__(self, circuit: Circuit, source: VectorInput) -> None:
        super().__init__(circuit, source)
        self._source = source

    @property
    def name(self) -> str:
        return self._source.name

    def sample(self) -> SignalsExpr:
        offset = self._circuit.now.offset
        if offset == 0:
            return self
        return SignalsExpr(self._circuit, self._circuit._sample_vector_input(self._source, offset))


class Circuit(_Circuit):
    """Public circuit builder with runtime-open whole-vector expressions."""

    def signals(self, name: str) -> _BaseSignalsInput:
        self._claim_name(name, "input")
        value = VectorInput(name)
        self._vector_inputs.append(value)
        return cast(_BaseSignalsInput, SignalsInput(self, value))

    def constant_signals(self, signals: dict[SignalId, int]) -> SignalsExpr:
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


__all__ = [
    "AccumulatorReg",
    "Circuit",
    "CircuitBuildError",
    "Expr",
    "FreezeReg",
    "Input",
    "LogicalTime",
    "SignalsExpr",
    "SignalsInput",
]
