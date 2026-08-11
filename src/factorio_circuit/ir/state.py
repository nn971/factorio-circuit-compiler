"""Logical whole-vector state primitives.

State accesses carry elaboration-order and freshness metadata even though the current physical
prototypes still realize a restricted subset of those semantics.  Keeping the metadata in the IR
lets a later frontend relax strict ordering without replacing the state representation.
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

    ``set`` high makes the register transparent (track input); ``set`` low freezes
    the last tracked vector.
    """

    name: str


StateRegister = AccumulatorRegister | FreezeRegister


@dataclass(frozen=True, slots=True)
class VectorRegisterRead:
    """Observation of a whole-vector register at one logical freshness point."""

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
