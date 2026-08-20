"""Semantic oracle sources.

An oracle is an observable Level source whose value is not computed by the
deterministic semantic model.  Reference simulation supplies oracle traces
explicitly, while physical compilation binds each oracle to a target provider.

Oracle sources deliberately subclass the ordinary compatibility input records.
This keeps the existing clock normalization and expression algebra unchanged
while preserving enough identity to keep external ports and compiler-owned
physical observations distinct at module boundaries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factorio_circuit.ir.semantic import Input, VectorInput

if TYPE_CHECKING:
    from factorio_circuit.ir.semantic import CircuitModule


class OracleInput(Input):
    """Scalar Level oracle source."""

    __slots__ = ()


class VectorOracleInput(VectorInput):
    """Whole-vector Level oracle source."""

    __slots__ = ()


OracleSource = OracleInput | VectorOracleInput


def oracle_sources(module: CircuitModule) -> tuple[OracleSource, ...]:
    """Return declared scalar/vector oracles in a stable scalar-then-vector order."""

    return (
        *(item for item in module.inputs if isinstance(item, OracleInput)),
        *(item for item in module.vector_inputs if isinstance(item, VectorOracleInput)),
    )


def oracle_names(module: CircuitModule) -> frozenset[str]:
    """Return all declared oracle names."""

    return frozenset(source.name for source in oracle_sources(module))
