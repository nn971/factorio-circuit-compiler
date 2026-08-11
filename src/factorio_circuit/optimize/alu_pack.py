"""Factorio-specific ALU lane-packing helpers."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.semantic import BinaryOp, CircuitModule
from factorio_circuit.optimize.partition import partition_arithmetic


@dataclass(frozen=True, slots=True)
class PackedArithmeticGroup:
    operation: str
    constant: int
    operations: tuple[BinaryOp, ...]


def find_packable_arithmetic(module: CircuitModule) -> tuple[PackedArithmeticGroup, ...]:
    """Compatibility groups exposed for diagnostics and tests."""

    result: list[PackedArithmeticGroup] = []
    for partition in partition_arithmetic(module):
        if partition.key is None:
            op = partition.operations[0]
            result.append(PackedArithmeticGroup(op.op, 0, partition.operations))
        else:
            result.append(
                PackedArithmeticGroup(
                    partition.key.operation,
                    partition.key.constant,
                    partition.operations,
                )
            )
    return tuple(result)
