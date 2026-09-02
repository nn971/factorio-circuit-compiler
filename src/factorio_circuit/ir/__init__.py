"""Intermediate-representation package helpers.

The semantic frontend constructs immutable expression DAGs. Flow validation must therefore walk
those DAGs as DAGs, not repeatedly expand shared subexpressions as trees. The semantic module keeps
its public recursive implementation; this package wrapper supplies one identity memo for each
outer flow-facts query and collapses ``ensure_expression_flow`` to a single validated traversal.

This matters for legacy Level expressions in particular: before normalization they often have no
concrete clock, so there is no ``Flow`` object available to use as a persistent per-node cache.
Per-query memoization still bounds each validation/inference pass by the number of distinct nodes in
the reachable DAG.

Frontend-built canonical state transitions also retain their originating legacy state operation in
``StateTransition.legacy``. Prefer that exact provenance when checking canonical/legacy duplicate
representations; only fall back to structural signatures for independently reconstructed modules.
That avoids recursively hashing deep shared expression DAGs during ``Circuit.build()``.

Canonical validation likewise traverses immutable shared expressions. Cache scalar/vector
validation by node identity plus expected structural clock for one module-validation pass. Including
the expected clock preserves cross-clock mismatch checks while preventing declared operations from
revalidating the same deep DAG many times.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import cast

from . import semantic as _semantic
from . import state as _state

_original_flow_facts = _semantic._flow_facts
_FlowFactsMemo = dict[tuple[int, _semantic.PayloadShape], _semantic.FlowFacts]
_flow_facts_memo: ContextVar[_FlowFactsMemo | None] = ContextVar(
    "factorio_circuit_flow_facts_memo", default=None
)


def _memoized_flow_facts(value: object, expected: _semantic.PayloadShape) -> _semantic.FlowFacts:
    memo = _flow_facts_memo.get()
    token: Token[_FlowFactsMemo | None] | None = None
    if memo is None:
        memo = {}
        token = _flow_facts_memo.set(memo)

    key = (id(value), expected)
    try:
        cached = memo.get(key)
        if cached is not None:
            return cached
        result = _original_flow_facts(value, expected)
        memo[key] = result
        return result
    finally:
        if token is not None:
            _flow_facts_memo.reset(token)


def _single_pass_ensure_expression_flow(
    value: object,
    expected: _semantic.PayloadShape | None = None,
) -> _semantic.FlowFacts:
    """Validate once and attach the same inferred metadata as the original two-pass helper."""

    shape = expected or (
        _semantic.PayloadShape.VECTOR
        if _semantic.is_vector_value(value)
        else _semantic.PayloadShape.SCALAR
    )
    facts = _semantic.validate_expression_flow(value, shape)
    if (
        facts.modality is not None
        and facts.clock is not None
        and getattr(value, "flow", None) is None
    ):
        object.__setattr__(
            value,
            "flow",
            _semantic.Flow(None, facts.shape, facts.modality, facts.clock),
        )
    return facts


_original_state_transitions = _state.state_transitions


def _provenance_first_state_transitions(module: object) -> tuple[_state.StateTransition, ...]:
    """Use exact legacy-operation provenance before expensive structural duplicate checks."""

    transitions = tuple(getattr(module, "transitions", ()))
    if transitions and all(isinstance(item, _state.StateTransition) for item in transitions):
        canonical = cast(tuple[_state.StateTransition, ...], transitions)
        legacy = _state._legacy_state_transitions(module)
        if legacy:
            canonical_legacy_ids = {
                id(item.legacy) for item in canonical if item.legacy is not None
            }
            if all(
                item.legacy is not None and id(item.legacy) in canonical_legacy_ids
                for item in legacy
            ):
                return canonical
    return _original_state_transitions(module)


_CanonicalValidationKey = tuple[str, int, _semantic.Clock | None]
_canonical_validation_memo: ContextVar[dict[_CanonicalValidationKey, _semantic.Flow] | None] = (
    ContextVar("factorio_circuit_canonical_validation_memo", default=None)
)
_original_validate_canonical_scalar = _semantic._validate_canonical_scalar
_original_validate_canonical_vector = _semantic._validate_canonical_vector
_original_validate_canonical_module = _semantic.validate_canonical_module


def _memoized_validate_canonical_scalar(
    value: object,
    register_clocks: dict[_state.StateRegister, _semantic.Clock],
    expected: _semantic.Clock | None = None,
) -> _semantic.Flow:
    memo = _canonical_validation_memo.get()
    token: Token[dict[_CanonicalValidationKey, _semantic.Flow] | None] | None = None
    if memo is None:
        memo = {}
        token = _canonical_validation_memo.set(memo)

    key: _CanonicalValidationKey = ("scalar", id(value), expected)
    try:
        cached = memo.get(key)
        if cached is not None:
            return cached
        result = _original_validate_canonical_scalar(value, register_clocks, expected)
        memo[key] = result
        return result
    finally:
        if token is not None:
            _canonical_validation_memo.reset(token)


def _memoized_validate_canonical_vector(
    value: object,
    register_clocks: dict[_state.StateRegister, _semantic.Clock],
    expected: _semantic.Clock | None = None,
) -> _semantic.Flow:
    memo = _canonical_validation_memo.get()
    token: Token[dict[_CanonicalValidationKey, _semantic.Flow] | None] | None = None
    if memo is None:
        memo = {}
        token = _canonical_validation_memo.set(memo)

    key: _CanonicalValidationKey = ("vector", id(value), expected)
    try:
        cached = memo.get(key)
        if cached is not None:
            return cached
        result = _original_validate_canonical_vector(value, register_clocks, expected)
        memo[key] = result
        return result
    finally:
        if token is not None:
            _canonical_validation_memo.reset(token)


def _memoized_validate_canonical_module(module: _semantic.CircuitModule) -> None:
    memo = _canonical_validation_memo.get()
    token: Token[dict[_CanonicalValidationKey, _semantic.Flow] | None] | None = None
    if memo is None:
        token = _canonical_validation_memo.set({})
    try:
        _original_validate_canonical_module(module)
    finally:
        if token is not None:
            _canonical_validation_memo.reset(token)


_semantic._flow_facts = _memoized_flow_facts
_semantic.ensure_expression_flow = _single_pass_ensure_expression_flow
_state.state_transitions = _provenance_first_state_transitions
_semantic._validate_canonical_scalar = _memoized_validate_canonical_scalar
_semantic._validate_canonical_vector = _memoized_validate_canonical_vector
_semantic.validate_canonical_module = _memoized_validate_canonical_module
