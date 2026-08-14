"""Internal runtime-open vector expression nodes."""

from dataclasses import dataclass

from factorio_circuit.ir.semantic import ScalarValue, VectorValue


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
class _VectorSelect(_VectorFilter):
    select_max: bool = True
    index: int = 0


_VectorNode = _VectorBinaryOp | _VectorScalarOp | _VectorFilter | _VectorSelect
