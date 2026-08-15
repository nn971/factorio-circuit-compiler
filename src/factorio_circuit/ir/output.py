"""Semantic output-boundary materialization contracts.

Clocked flows remain sparse internally.  This module records how each exported flow is projected
onto a dense external circuit-network interface without changing the Flow itself.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from factorio_circuit.ir.semantic import (
    CircuitModule,
    OutputValue,
    ReturnValue,
    TemporalModality,
    validate_expression_flow,
)


class OutputMaterializationPolicy(StrEnum):
    """Dense boundary behavior between semantic clock activations."""

    HOLD = "hold"
    ZERO = "zero"
    VALID = "valid"


@dataclass(frozen=True, slots=True)
class OutputMaterialization:
    """Materialization contract for one payload output.

    ``valid_name`` is present exactly for ``VALID`` outputs and names the companion presence output.
    The valid stream is semantically 1 exactly when the payload flow is present and 0 otherwise.
    """

    policy: OutputMaterializationPolicy
    valid_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, OutputMaterializationPolicy):
            raise ValueError("output materialization policy must be an OutputMaterializationPolicy")
        if self.policy is OutputMaterializationPolicy.VALID:
            if self.valid_name is not None and not self.valid_name:
                raise ValueError("VALID companion output name must be non-empty")
        elif self.valid_name is not None:
            raise ValueError("only VALID materialization may declare a companion valid output")


@dataclass(frozen=True, slots=True)
class MaterializedReturnValue(ReturnValue):
    """ReturnValue carrying explicit dense-boundary contracts for every exported payload."""

    materializations: tuple[OutputMaterialization, ...] = ()

    def __post_init__(self) -> None:
        # ``@dataclass(slots=True)`` returns a replacement class object.  Zero-argument ``super()``
        # can therefore retain the pre-transformation class in its ``__class__`` cell and fail at
        # runtime on current Python versions.  Call the dataclass base explicitly here.
        ReturnValue.__post_init__(self)
        if self.materializations and len(self.materializations) != len(self.values):
            raise ValueError("output materializations must match output values")

        contracts = self.materializations or tuple(
            infer_output_materialization(value, self._resolved_name(index))
            for index, value in enumerate(self.values)
        )
        normalized: list[OutputMaterialization] = []
        for index, contract in enumerate(contracts):
            if not isinstance(contract, OutputMaterialization):
                raise ValueError("output materializations must be OutputMaterialization values")
            if contract.policy is OutputMaterializationPolicy.VALID and contract.valid_name is None:
                contract = OutputMaterialization(
                    OutputMaterializationPolicy.VALID,
                    f"{self._resolved_name(index)}__valid",
                )
            normalized.append(contract)

        payload_names = [self._resolved_name(index) for index in range(len(self.values))]
        valid_names = [
            contract.valid_name for contract in normalized if contract.valid_name is not None
        ]
        all_names = [*payload_names, *valid_names]
        if len(set(all_names)) != len(all_names):
            raise ValueError("payload and companion valid output names must be unique")

        object.__setattr__(self, "materializations", tuple(normalized))

    def _resolved_name(self, index: int) -> str:
        if self.names:
            declared = self.names[index]
            if declared:
                return declared
        candidate = getattr(self.values[index], "name", None)
        return candidate or f"out{index}"

    def contract_for(self, index: int) -> OutputMaterialization:
        return self.materializations[index]


def infer_output_materialization(value: OutputValue, name: str) -> OutputMaterialization:
    """Infer the conservative boundary default from a flow's modality.

    Additive Event knowledge is intentionally supplied by the frontend where the bridge/source kind
    is explicit.  A generic Event defaults to VALID so zero-valued occurrences remain
    distinguishable from absence.  Level and occurrence-invariant outputs default to HOLD.
    """

    facts = validate_expression_flow(value)
    if facts.modality is TemporalModality.EVENT:
        return OutputMaterialization(OutputMaterializationPolicy.VALID, f"{name}__valid")
    return OutputMaterialization(OutputMaterializationPolicy.HOLD)


def materialized_return_value(
    output: ReturnValue,
    materializations: tuple[OutputMaterialization, ...] | None = None,
) -> MaterializedReturnValue:
    """Attach or preserve output contracts without changing payload values or names."""

    if materializations is None and isinstance(output, MaterializedReturnValue):
        return output
    contracts = (
        output.materializations
        if materializations is None and isinstance(output, MaterializedReturnValue)
        else materializations or ()
    )
    return MaterializedReturnValue(output.values, output.names, contracts)


def output_materializations(output: ReturnValue) -> tuple[OutputMaterialization, ...]:
    """Return explicit contracts, conservatively inferring them for compatibility ReturnValues."""

    return materialized_return_value(output).materializations


def preserve_output_materializations(
    module: CircuitModule,
    source_output: ReturnValue,
) -> CircuitModule:
    """Reattach boundary contracts after a compatibility pass rebuilds ReturnValue.

    Semantic optimization/normalization may replace payload expression objects but preserves output
    ordering.  Materialization is an external-boundary property, so preserving it by output position
    is intentional and independent of expression rewriting.
    """

    contracts = output_materializations(source_output)
    if len(contracts) != len(module.output.values):
        raise ValueError(
            "output rewriting changed arity while preserving materialization contracts"
        )
    return replace(
        module,
        output=MaterializedReturnValue(module.output.values, module.output.names, contracts),
    )
