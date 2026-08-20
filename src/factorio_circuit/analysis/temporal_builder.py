"""Timing-exact builder for the temporal computation hypergraph.

The semantic timing model gives scalar ``Select`` asymmetric dependency latencies: data arms use
the conservative three-stage generic path while the condition uses the two-stage condition path.
Keep that distinction here so global placement searches exactly the same feasible timing region as
``state_timing``.  The core temporal hypergraph records and diagnostics remain in
:mod:`factorio_circuit.analysis.temporal_hypergraph`.
"""

from __future__ import annotations

from factorio_circuit.ir.semantic import CircuitModule, Select
from factorio_circuit.sampling import SamplingPolicy

from .latency import FACTORIO_LATENCY
from .state_timing import StateTimingPlan
from .temporal_hypergraph import TemporalHypergraph, _TemporalHypergraphBuilder


class _TimingExactTemporalHypergraphBuilder(_TemporalHypergraphBuilder):
    """Build hypergraphs using the authoritative per-input target latency envelope."""

    @staticmethod
    def _children(value: object) -> tuple[tuple[object, int], ...]:
        if isinstance(value, Select):
            condition_latency = FACTORIO_LATENCY.operation_latency(
                "select_condition", value.name
            )
            data_latency = FACTORIO_LATENCY.operation_latency("select_data", value.name)
            return (
                (value.condition, condition_latency),
                (value.when_true, data_latency),
                (value.when_false, data_latency),
            )
        return _TemporalHypergraphBuilder._children(value)


def build_temporal_hypergraph(
    module: CircuitModule,
    timing: StateTimingPlan,
    *,
    sampling_policy: SamplingPolicy = SamplingPolicy.BEGINNING_OF_STEP,
) -> TemporalHypergraph:
    """Build the periodic state-cone hypergraph with state-timing-exact edge latencies."""

    if not isinstance(sampling_policy, SamplingPolicy):
        raise TypeError("sampling_policy must be a SamplingPolicy")
    return _TimingExactTemporalHypergraphBuilder(module, timing, sampling_policy).build()


__all__ = ["build_temporal_hypergraph"]
