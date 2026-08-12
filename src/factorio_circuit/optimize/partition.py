"""Greedy compatibility partitioning for arithmetic operations."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.semantic import BinaryOp, CircuitModule
from factorio_circuit.optimize.compatibility import (
    ArithmeticCompatibilityKey,
    arithmetic_compatibility_key,
)


@dataclass(frozen=True, slots=True)
class ArithmeticPartition:
    key: ArithmeticCompatibilityKey | None
    operations: tuple[BinaryOp, ...]


def partition_arithmetic(module: CircuitModule) -> tuple[ArithmeticPartition, ...]:
    """Partition arithmetic nodes by the current compatibility relation.

    This is intentionally a simple bucket/greedy baseline. The API gives future graph/ILP/SMT
    partitioners a stable place to plug in without changing lowering.
    """

    buckets: dict[ArithmeticCompatibilityKey, list[BinaryOp]] = {}
    singles: list[BinaryOp] = []
    for value in module.operations:
        if not isinstance(value, BinaryOp):
            continue
        key = arithmetic_compatibility_key(value)
        if key is None:
            singles.append(value)
        else:
            buckets.setdefault(key, []).append(value)

    groups = [ArithmeticPartition(key, tuple(values)) for key, values in buckets.items()]
    groups.extend(ArithmeticPartition(None, (value,)) for value in singles)
    return tuple(groups)
