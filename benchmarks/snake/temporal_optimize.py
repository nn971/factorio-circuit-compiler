"""Analyze and solve the Snake temporal computation hypergraph without building a layout.

Examples::

    uv run python -m benchmarks.snake.temporal_optimize
    uv run --with 'ortools>=9.14,<10' python -m benchmarks.snake.temporal_optimize --solve

The first command prints the current abstract-physical delay census plus ASAP/ALAP hypergraph
transport estimates. ``--solve`` invokes the optional OR-Tools CP-SAT backend and searches jointly
for computation phases and continuous scalar delay-bus groups at the already-inferred Snake period.
It does not yet rewrite the blueprint; this is the proof/measurement stage before physical bus
realization is connected to lowering.
"""

from __future__ import annotations

import argparse
import sys

from benchmarks.snake.random_model import FOOD_CANDIDATE_ORACLE, build_random_snake_circuit
from factorio_circuit import RandomSignalOracleProvider, SamplingPolicy, lower_to_abstract_physical
from factorio_circuit.analysis import (
    build_temporal_hypergraph,
    census_abstract_physical,
    census_phase_delays,
    format_abstract_physical_census,
    format_phase_delay_census,
    format_temporal_hypergraph,
    format_temporal_optimization,
    optimize_temporal_hypergraph,
)
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sampling-policy",
        choices=[policy.value for policy in SamplingPolicy],
        default=SamplingPolicy.ALAP.value,
        help="external Level observation policy used by the baseline lowering (default: alap)",
    )
    parser.add_argument(
        "--solve",
        action="store_true",
        help="run the optional exact CP-SAT phase/bus search",
    )
    parser.add_argument(
        "--bus-capacity",
        type=int,
        default=len(DEFAULT_VIRTUAL_SIGNAL_POOL),
        help=(
            "maximum abstract scalar lanes assigned to one continuous delay bus "
            f"(default: {len(DEFAULT_VIRTUAL_SIGNAL_POOL)})"
        ),
    )
    parser.add_argument(
        "--max-buses",
        type=int,
        help="optional upper bound on scalar delay buses; default allows one per candidate",
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=60.0,
        help="CP-SAT wall-clock limit in seconds (default: 60)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="CP-SAT search workers; 1 is deterministic (default: 1)",
    )
    args = parser.parse_args()

    sampling_policy = SamplingPolicy(args.sampling_policy)
    circuit = build_random_snake_circuit()
    lowered = lower_to_abstract_physical(
        circuit,
        optimize=False,
        oracle_providers={
            FOOD_CANDIDATE_ORACLE: RandomSignalOracleProvider(update_interval=1),
        },
        sampling_policy=sampling_policy,
    )

    print(format_abstract_physical_census(census_abstract_physical(lowered.abstract_physical)))
    print()
    print(format_phase_delay_census(census_phase_delays(lowered.abstract_physical)))
    print()

    graph = build_temporal_hypergraph(
        lowered.optimized_ir,
        lowered.state_timing,
        sampling_policy=sampling_policy,
    )
    print(format_temporal_hypergraph(graph))

    if not args.solve:
        print(
            "\nexact search not requested; rerun with `uv run --with 'ortools>=9.14,<10' "
            "python -m benchmarks.snake.temporal_optimize --solve`",
            file=sys.stderr,
        )
        return

    result = optimize_temporal_hypergraph(
        graph,
        bus_capacity=args.bus_capacity,
        max_buses=args.max_buses,
        time_limit_seconds=args.time_limit,
        workers=args.workers,
    )
    print()
    print(format_temporal_optimization(result))


if __name__ == "__main__":
    main()
