"""Circuit builder hooks for runtime-open whole vectors."""

from __future__ import annotations

from typing import cast

from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import VectorConstant, VectorInput
from factorio_circuit.ir.state import VectorRegisterRead

from .symbolic import AccumulatorReg as _BaseAccumulatorReg
from .symbolic import Circuit as _Circuit
from .symbolic import CircuitBuildError
from .symbolic import FreezeReg as _BaseFreezeReg
from .symbolic import SignalsInput as _BaseSignalsInput
from .vector_expr import SignalsExpr


class SignalsInput(SignalsExpr):
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


class AccumulatorReg(_BaseAccumulatorReg):
    """Accumulator register whose reads retain runtime-open vector operations."""

    @property
    def value(self) -> SignalsExpr:
        read = VectorRegisterRead(
            register=self._register,
            offset=self._circuit.now.offset,
            order=self._circuit._next_state_order(self._register),
            name=self._register.name,
        )
        return SignalsExpr(self._circuit, read)


class FreezeReg(_BaseFreezeReg):
    """Freeze register whose reads retain runtime-open vector operations."""

    @property
    def value(self) -> SignalsExpr:
        read = VectorRegisterRead(
            register=self._register,
            offset=self._circuit.now.offset,
            order=self._circuit._next_state_order(self._register),
            name=self._register.name,
        )
        return SignalsExpr(self._circuit, read)


class Circuit(_Circuit):
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

    def accumulator(self, name: str | None = None) -> AccumulatorReg:
        return AccumulatorReg(self, name=name)

    def freeze(self, name: str | None = None) -> FreezeReg:
        return FreezeReg(self, name=name)
