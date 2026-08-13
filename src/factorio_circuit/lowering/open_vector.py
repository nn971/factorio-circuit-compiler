"""Whole-vector realization extension for the standard abstract lowerer."""

from factorio_circuit.frontend import (
    _VectorBinaryOp,
    _VectorFilter,
    _VectorScalarOp,
    _VectorSelect,
)
from factorio_circuit.ir.semantic import VectorValue
from factorio_circuit.lowering.ir_to_abstract_physical import AbstractPhysicalLowerer as _Base
from factorio_circuit.lowering.ir_to_abstract_physical import RealizedVector

from .vector_binary import realize_vector_binary
from .vector_select import realize_vector_select
from .vector_unary import realize_vector_filter, realize_vector_scalar


class VectorLowerer(_Base):
    def realize_vector(self, value: VectorValue) -> RealizedVector:
        item: object = value
        cached = self.vector_memo.get(id(item))
        if cached is not None:
            return cached
        if isinstance(item, _VectorBinaryOp):
            result = realize_vector_binary(self, item)
        elif isinstance(item, _VectorScalarOp):
            result = realize_vector_scalar(self, item)
        elif isinstance(item, _VectorSelect):
            result = realize_vector_select(self, item)
        elif isinstance(item, _VectorFilter):
            result = realize_vector_filter(self, item)
        else:
            return super().realize_vector(value)
        self.vector_memo[id(item)] = result
        return result
