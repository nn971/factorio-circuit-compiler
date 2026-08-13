"""Greedy compatibility partitioning for arithmetic operations."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.semantic import BinaryOp, CircuitModule, Constant, Value, dependencies
from factorio_circuit.optimize.compatibility import (
    ArithmeticCompatibilityKey,
    arithmetic_compatibility_key,
)
from factorio_circuit.target.factorio.semantics import apply_binary


@dataclass(frozen=True, slots=True)
class ArithmeticPartition:
    key: ArithmeticCompatibilityKey | None
    operations: tuple[BinaryOp, ...]


@dataclass(frozen=True, slots=True)
class PairwiseArithmeticPartition:
    """Independent dynamic-dynamic operations safe to consider as one Each/Each batch."""

    operation: str
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


def partition_pairwise_arithmetic(
    module: CircuitModule,
) -> tuple[PairwiseArithmeticPartition, ...]:
    """Bucket dynamic-dynamic arithmetic by operation and semantic dependency depth.

    Equal depth prevents a partition from containing an operation and one of its own
    transitive consumers. Physical lowering performs the remaining phase, lane, and
    red/green compatibility checks before emitting a packed combinator.
    """

    depth_cache: dict[int, int] = {}

    def depth(value: Value) -> int:
        cached = depth_cache.get(id(value))
        if cached is not None:
            return cached
        children = dependencies(value)
        result = 0 if not children else 1 + max(depth(child) for child in children)
        depth_cache[id(value)] = result
        return result

    buckets: dict[tuple[str, int], list[BinaryOp]] = {}
    for value in module.operations:
        if not isinstance(value, BinaryOp):
            continue
        if isinstance(value.left, Constant) or isinstance(value.right, Constant):
            continue
        # Each/Each skips a lane only when it is zero on both selected networks.
        # Operations where f(0, 0) != 0 therefore cannot preserve scalar semantics.
        if apply_binary(value.op, 0, 0) != 0:
            continue
        buckets.setdefault((value.op, depth(value)), []).append(value)

    return tuple(
        PairwiseArithmeticPartition(operation, tuple(values))
        for (operation, _depth), values in buckets.items()
    )
