"""Public oracle declarations layered on the clocked frontend."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.ir.oracle import OracleInput, VectorOracleInput

from .clock_bridges import Circuit as _Circuit
from .vector_circuit import Input, SignalsInput

if TYPE_CHECKING:
    from factorio_circuit.compiler import CompilationResult
    from factorio_circuit.oracles import OracleProvider


class Oracle(Input):
    """Scalar Level value supplied by an oracle provider rather than an external input port."""

    __slots__ = ()

    @property
    def ir(self) -> OracleInput:
        return self._source  # type: ignore[return-value]


class SignalsOracle(SignalsInput):
    """Whole-vector Level value supplied by an oracle provider."""

    __slots__ = ()

    @property
    def ir(self) -> VectorOracleInput:
        return self._source  # type: ignore[return-value]


class Circuit(_Circuit):
    """Clocked frontend with explicit semantic oracle declarations."""

    def oracle(self, name: str) -> Oracle:
        """Declare a scalar Level oracle.

        Oracle values participate in deterministic expressions exactly like Level inputs, but
        physical compilation requires an explicit provider binding instead of exposing an ordinary
        input port by accident.
        """

        self._claim_name(name, "oracle")
        source = OracleInput(name)
        self._inputs.append(source)
        return Oracle(self, source)

    def oracle_signals(self, name: str) -> SignalsOracle:
        """Declare a whole-vector Level oracle."""

        self._claim_name(name, "oracle")
        source = VectorOracleInput(name)
        self._vector_inputs.append(source)
        return SignalsOracle(self, source)

    def compile(
        self,
        *,
        optimize: bool = True,
        blueprint_safe_wire_span: float | None = None,
        oracle_providers: Mapping[str, OracleProvider] | None = None,
    ) -> CompilationResult:
        """Compile this circuit with explicit physical providers for every oracle."""

        from factorio_circuit.compiler import compile_circuit

        safe_span = (
            DEFAULT_SAFE_WIRE_SPAN if blueprint_safe_wire_span is None else blueprint_safe_wire_span
        )
        return compile_circuit(
            self,
            optimize=optimize,
            blueprint_safe_wire_span=safe_span,
            oracle_providers=oracle_providers,
        )
