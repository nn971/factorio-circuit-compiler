"""Compatibility relations used by physical packing experiments."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.semantic import BinaryOp, Constant, Value
from factorio_circuit.target.factorio.semantics import apply_binary


@dataclass(frozen=True, slots=True)
class ArithmeticCompatibilityKey:
    operation: str
    constant: int
    constant_side: str


def arithmetic_compatibility_key(op: BinaryOp) -> ArithmeticCompatibilityKey | None:
    """Describe arithmetic operations that can share one ``Each`` combinator.

    Phase I recognizes a common constant on either side. Physical lowering applies an additional
    safety test before packing because an ``Each`` input network must not contain unrelated lanes.
    """

    if isinstance(op.right, Constant) and apply_binary(op.op, 0, op.right.value) == 0:
        return ArithmeticCompatibilityKey(op.op, op.right.value, "right")
    if (
        isinstance(op.left, Constant)
        and op.op in {"+", "*", "&", "|", "^"}
        and apply_binary(op.op, op.left.value, 0) == 0
    ):
        return ArithmeticCompatibilityKey(op.op, op.left.value, "left")
    return None


def dynamic_operand(op: BinaryOp, key: ArithmeticCompatibilityKey) -> Value:
    return op.left if key.constant_side == "right" else op.right


# Compatibility alias kept for older callers/tests.
def arithmetic_common_constant_key(op: BinaryOp) -> tuple[str, int] | None:
    key = arithmetic_compatibility_key(op)
    return None if key is None else (key.operation, key.constant)
