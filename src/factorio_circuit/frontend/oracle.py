"""Public oracle declarations layered on the clocked frontend."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from factorio_circuit.blueprint.routing import DEFAULT_SAFE_WIRE_SPAN
from factorio_circuit.ir.oracle import (
    OracleInput,
    VectorOracleInput,
    provider_input_port_name,
)
from factorio_circuit.ir.semantic import CircuitModule, OutputValue, ReturnValue

from .clock_bridges import Circuit as _Circuit
from .vector_circuit import Expr, Input, SignalsInput
from .vector_expr import SignalsExpr

if TYPE_CHECKING:
    from factorio_circuit.compiler import CompilationResult
    from factorio_circuit.oracles import OracleProvider
    from factorio_circuit.progress import ProgressCallback
    from factorio_circuit.synthesis.placement import PlacementOptions, Position


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

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._oracle_provider_inputs: list[tuple[str, str, OutputValue]] = []

    def oracle(self, name: str) -> Oracle:
        """Declare a scalar Level oracle."""

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

    def bind_oracle_input(
        self,
        oracle: Oracle | SignalsOracle,
        name: str,
        value: Expr | SignalsExpr,
    ) -> None:
        """Expose a deterministic expression as a named physical input to ``oracle``'s provider.

        The tap is not a public semantic output. ``Circuit.compile()`` temporarily lowers it through
        the ordinary output path so the provider receives the exact synthesized net, then removes
        that internal boundary before the final layout is returned.
        """

        if not isinstance(oracle, (Oracle, SignalsOracle)) or oracle._circuit is not self:
            raise ValueError("oracle provider inputs require an oracle declared by this Circuit")
        if not isinstance(name, str) or not name:
            raise ValueError("oracle provider input name must be non-empty")
        if not isinstance(value, (Expr, SignalsExpr)):
            raise ValueError("oracle provider inputs require a scalar or vector expression")
        self._require_owned(value)
        key = (oracle.name, name)
        if any(
            (provider, input_name) == key
            for provider, input_name, _value in self._oracle_provider_inputs
        ):
            raise ValueError(f"oracle provider input {oracle.name!r}/{name!r} is already bound")
        self._oracle_provider_inputs.append((oracle.name, name, value.ir))

    def _build_for_physical(self) -> CircuitModule:
        module = super().build()
        if not self._oracle_provider_inputs:
            return module
        hidden_values = tuple(value for _oracle, _name, value in self._oracle_provider_inputs)
        hidden_names = tuple(
            provider_input_port_name(oracle, name)
            for oracle, name, _value in self._oracle_provider_inputs
        )
        return replace(
            module,
            output=ReturnValue(
                (*module.output.values, *hidden_values),
                (*module.output.names, *hidden_names),
            ),
        )

    @staticmethod
    def _strip_provider_outputs(module: CircuitModule, count: int) -> CircuitModule:
        if count == 0:
            return module
        if count > len(module.output.values):
            raise ValueError("compiled module lost oracle provider input outputs")
        return replace(
            module,
            output=ReturnValue(module.output.values[:-count], module.output.names[:-count]),
        )

    def compile(
        self,
        *,
        optimize: bool = True,
        blueprint_safe_wire_span: float | None = None,
        placement: PlacementOptions | None = None,
        physical_anchors: Mapping[str, Position] | None = None,
        oracle_providers: Mapping[str, OracleProvider] | None = None,
        progress: ProgressCallback | None = None,
    ) -> CompilationResult:
        """Compile this circuit with explicit physical providers for every oracle."""

        from factorio_circuit.compiler import compile_circuit

        safe_span = (
            DEFAULT_SAFE_WIRE_SPAN if blueprint_safe_wire_span is None else blueprint_safe_wire_span
        )
        provider_input_count = len(self._oracle_provider_inputs)
        result = compile_circuit(
            self._build_for_physical(),
            optimize=optimize,
            blueprint_safe_wire_span=safe_span,
            placement=placement,
            physical_anchors=physical_anchors,
            oracle_providers=oracle_providers,
            progress=progress,
        )
        if provider_input_count == 0:
            return result
        return replace(
            result,
            semantic_ir=self._strip_provider_outputs(result.semantic_ir, provider_input_count),
            optimized_ir=self._strip_provider_outputs(result.optimized_ir, provider_input_count),
        )
