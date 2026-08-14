"""Logical whole-vector state primitives.

State accesses carry elaboration order and logical-step metadata. Physical Factorio phases and clock
periods are inferred later and are intentionally absent from this IR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factorio_circuit.ir.semantic import EventInput, ScalarValue, VectorValue


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


@dataclass(frozen=True, slots=True)
class FreezeCapture:
    """Semantic-only capture of a vector value on an external Event occurrence."""

    register: FreezeRegister
    trigger: EventInput
    value: VectorValue | None
    required_min_separation: int
    order: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.required_min_separation, bool) or not isinstance(
            self.required_min_separation, int
        ):
            raise ValueError("capture minimum separation must be an integer")
        if self.required_min_separation < 1:
            raise ValueError("capture minimum separation must be positive")


StateOperation = AccumulatorAdd | AccumulatorClear | FreezeSet
EventStateOperation = FreezeCapture
