"""Circuit builder hooks for runtime-open vectors and flow-local logical-step timing."""

from __future__ import annotations

from typing import cast

from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import (
    BinaryOp,
    Compare,
    DerivedValue,
    Flow,
    Input as IRInput,
    PayloadShape,
    ScalarValue,
    Select,
    VectorConstant,
    VectorInput,
)
from factorio_circuit.ir.state import VectorRegisterRead

from .reindex import FlowStepError, reindex_scalar
from .symbolic import AccumulatorReg as _BaseAccumulatorReg
from .symbolic import Circuit as _Circuit
from .symbolic import CircuitBuildError
from .symbolic import Expr as _BaseExpr
from .symbolic import FreezeReg as _BaseFreezeReg
from .symbolic import Input as _BaseInput
from .vector_expr import SignalsExpr


class Expr(_BaseExpr):
    """Public scalar expression with flow-local logical reindexing."""

    __slots__ = ()

    def step(self, n: int = 1) -> Expr:
        """Refer to this Level flow ``n`` logical clock occurrences later.

        ``step`` is pure logical reindexing: it leaves ``Circuit.now`` unchanged and never inserts a
        register or physical delay.  The legacy circuit-wide cursor remains available temporarily
        for compatibility with existing circuits.
        """

        try:
            value = reindex_scalar(self._value, n)
        except FlowStepError as exc:
            raise CircuitBuildError(str(exc)) from exc
        if value is self._value:
            return self
        if isinstance(value, (BinaryOp, Compare, Select)):
            return cast(Expr, self._circuit._derived(cast(DerivedValue, value)))
        return Expr(self._circuit, value)


class Input(_BaseInput, Expr):
    """Scalar Level source with both compatibility sampling and flow-local ``step``."""

    __slots__ = ()

    def sample(self) -> Expr:
        """Observe this external source at the circuit compatibility cursor."""

        offset = self._circuit.now.offset
        if offset == 0:
            return self
        return Expr(self._circuit, self._circuit._sample_scalar_input(self._source, offset))


class SignalsInput(SignalsExpr):
    __slots__ = ("_source",)

    def __init__(self, circuit: Circuit, source: VectorInput) -> None:
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
        """Observe this external vector at the current compatibility cursor."""

        offset = self._circuit.now.offset
        if offset == 0:
            return self
        return SignalsExpr(self._circuit, self._circuit._sample_vector_input(self._source, offset))


class AccumulatorReg(_BaseAccumulatorReg):
    """Accumulator register whose logical observations retain vector operations."""

    def sample(self) -> SignalsExpr:
        """Observe the accumulator state at the current logical step."""

        read = VectorRegisterRead(
            register=self._register,
            offset=self._circuit.now.offset,
            order=self._circuit._next_state_order(self._register),
            name=self._register.name,
        )
        return SignalsExpr(self._circuit, read)

    @property
    def value(self) -> SignalsExpr:
        """Compatibility alias for :meth:`sample`; new code should use ``sample()``."""

        return self.sample()


class FreezeReg(_BaseFreezeReg):
    """Freeze register whose logical observations retain vector operations."""

    def sample(self) -> SignalsExpr:
        """Observe the held state at the current logical step."""

        read = VectorRegisterRead(
            register=self._register,
            offset=self._circuit.now.offset,
            order=self._circuit._next_state_order(self._register),
            name=self._register.name,
        )
        return SignalsExpr(self._circuit, read)

    @property
    def value(self) -> SignalsExpr:
        """Compatibility alias for :meth:`sample`; new code should use ``sample()``."""

        return self.sample()


class Circuit(_Circuit):
    """Symbolic circuit whose compatibility cursor is measured in logical steps."""

    def input(self, name: str) -> Input:
        self._claim_name(name, "input")
        value = IRInput(name)
        self._inputs.append(value)
        return Input(self, value)

    def signals(self, name: str) -> SignalsInput:
        self._claim_name(name, "input")
        value = VectorInput(name)
        self._vector_inputs.append(value)
        return SignalsInput(self, value)

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

    def accumulator(self, name: str | None = None) -> AccumulatorReg:
        return AccumulatorReg(self, name=name)

    def freeze(self, name: str | None = None) -> FreezeReg:
        return FreezeReg(self, name=name)

    def _derived(self, value: DerivedValue) -> Expr:
        """Keep scalar derived results on the public flow-local Expr surface."""

        result = super()._derived(value)
        return Expr(self, cast(ScalarValue, result.ir))

    def step(self, n: int = 1) -> None:
        """Advance the legacy circuit-wide logical observation cursor by ``n`` steps.

        New code should prefer ``value.step(n)`` so logical indexing is local to the value being
        reindexed.  This method remains during migration to preserve existing circuit programs.
        """

        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise CircuitBuildError("step(n) requires a non-negative integer")
        self._freshness += n

    def step_until(self, n: int) -> None:
        """Advance the compatibility cursor to absolute logical step ``n``."""

        if isinstance(n, bool) or not isinstance(n, int) or n < self._freshness:
            raise CircuitBuildError(
                f"step_until(n) requires an integer n >= current logical step {self._freshness}"
            )
        self._freshness = n

    def tick(self, n: int = 1) -> None:
        """Reserve the physical-tick spelling for future explicit scheduling controls."""

        del n
        raise CircuitBuildError(
            "Circuit.tick() is reserved for future physical-tick control; use Circuit.step() "
            "to advance logical time"
        )

    def tick_until(self, n: int) -> None:
        """Reject the former logical-time spelling; use :meth:`step_until`."""

        del n
        raise CircuitBuildError(
            "Circuit.tick_until() no longer denotes logical time; use Circuit.step_until()"
        )
