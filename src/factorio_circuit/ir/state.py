"""Logical whole-vector state primitives.

State accesses carry elaboration order and logical-step metadata. Physical Factorio phases and clock
periods are inferred later and are intentionally absent from this IR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factorio_circuit.ir.semantic import ScalarValue, VectorValue


@dataclass(frozen=True, slots=True)
class AccumulatorRegister:
    """Whole-vector accumulator: memory += input; clear resets it."""

    name: str


@dataclass(frozen=True, slots=True)
class FreezeRegister:
    """Whole-vector sample/hold register.

    ``set`` high makes the register transparent at a logical update boundary; ``set`` low holds the
    previous logical state.
    """

    name: str


StateRegister = AccumulatorRegister | FreezeRegister


@dataclass(frozen=True, slots=True)
class VectorRegisterRead:
    """Observation of a whole-vector register at one logical step."""

    register: StateRegister
    offset: int = 0
    order: int = 0
    name: str | None = None


@dataclass(frozen=True, slots=True)
class AccumulatorAdd:
    register: AccumulatorRegister
    value: VectorValue
    when: ScalarValue
    order: int = 0


@dataclass(frozen=True, slots=True)
class AccumulatorClear:
    register: AccumulatorRegister
    when: ScalarValue
    order: int = 0


@dataclass(frozen=True, slots=True)
class FreezeSet:
    register: FreezeRegister
    value: VectorValue
    when: ScalarValue
    order: int = 0


StateOperation = AccumulatorAdd | AccumulatorClear | FreezeSet
