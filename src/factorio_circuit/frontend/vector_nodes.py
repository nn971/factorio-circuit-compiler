"""Compatibility aliases for the canonical public vector semantic nodes.

Vector nodes used to be owned by the symbolic frontend.  The semantic IR now owns them so a
normalized module does not depend on frontend-private classes.  The underscored aliases remain for
source compatibility with existing backend and user/test imports.
"""

from factorio_circuit.ir.semantic import (
    VectorBinaryOp,
    VectorFilter,
    VectorScalarOp,
    VectorSelect,
)

_VectorBinaryOp = VectorBinaryOp
_VectorScalarOp = VectorScalarOp
_VectorFilter = VectorFilter
_VectorSelect = VectorSelect
_VectorNode = VectorBinaryOp | VectorScalarOp | VectorFilter | VectorSelect

__all__ = [
    "VectorBinaryOp",
    "VectorFilter",
    "VectorScalarOp",
    "VectorSelect",
    "_VectorBinaryOp",
    "_VectorFilter",
    "_VectorNode",
    "_VectorScalarOp",
    "_VectorSelect",
]
