"""Runtime-open whole-vector expressions."""

from __future__ import annotations

from typing import cast

from factorio_circuit.events import EventCrossingError
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import Compare, Constant, VectorSignal, VectorValue

from .symbolic import CircuitBuildError, Expr, SampleOnReference
from .symbolic import SignalsExpr as _SignalsExpr
from .vector_nodes import (
    _VectorBinaryOp,
    _VectorFilter,
    _VectorNode,
    _VectorScalarOp,
    _VectorSelect,
)


class SignalsExpr(_SignalsExpr):
    """A whole Factorio signal vector with lane-wise runtime-open operations."""

    def _wrap_vector(self, value: _VectorNode) -> SignalsExpr:
        return SignalsExpr(self._circuit, cast(VectorValue, value))

    def _coerce_vector(self, other: object) -> VectorValue:
        if isinstance(other, SampleOnReference):
            raise EventCrossingError("SampleOn references cannot be used in vector expressions")
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
        return self._wrap_vector(_VectorSelect("select", self._value, 0))

    def any(self) -> Expr:
        """Return 1 exactly when at least one vector lane is nonzero."""
        anything = VectorSignal(self._value, SignalId("virtual", "signal-anything"))
        return self._circuit._derived(Compare("!=", anything, Constant(0)))

    def gate(self, condition: Expr | int | bool) -> SignalsExpr:
        """Pass the vector when ``condition`` is nonzero, otherwise produce the empty vector."""
        scalar = self._circuit._coerce_scalar(condition)
        active = scalar
        if not isinstance(scalar.ir, Compare):
            active = self._circuit._derived(Compare("!=", scalar.ir, Constant(0)))
        return self * active
