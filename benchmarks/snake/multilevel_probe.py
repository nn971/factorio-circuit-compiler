"""Inspect relay-blind multilevel coarsening on the full random-food Snake circuit.

This is an opt-in structural probe for Milestone C. It constructs the ordinary failproof safe-folded
Snake so that the exact physical implementation circuit is available, then builds a hierarchy using
only implementation electrical nets and fixed public markers. Routed relays and current distances do
not participate in clustering.
"""

from __future__ import annotations

import argparse
import json
from statistics import median

from benchmarks.snake.random_model import (
    DEFAULT_LOGICAL_STEPS_PER_MOVE,
    FOOD_CANDIDATE_ORACLE,
    build_random_snake_circuit,
)
from factorio_circuit import RandomSignalOracleProvider, SamplingPolicy
from factorio_circuit.synthesis.multilevel import build_multilevel_hierarchy
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-macros", type=int, default=32)
    args = parser.parse_args()
    if args.target_macros <= 0:
        parser.error("--target-macros must be positive")

    result = build_random_snake_circuit(
        logical_steps_per_move=DEFAULT_LOGICAL_STEPS_PER_MOVE
    ).compile(
        optimize=False,
        placement=safe_folded_crossbar_options(),
        oracle_providers={
            FOOD_CANDIDATE_ORACLE: RandomSignalOracleProvider(update_interval=1),
        },
        sampling_policy=SamplingPolicy.ALAP,
        progress=None,
    )
    fixed = frozenset(
        {
            *(port.marker_entity for port in result.physical_circuit.inputs),
            *(port.marker_entity for port in result.physical_circuit.outputs),
        }
    )
    hierarchy = build_multilevel_hierarchy(
        result.physical_circuit,
        fixed_entities=fixed,
        target_macros=args.target_macros,
    )

    levels = []
    for index, level in enumerate(hierarchy.levels):
        sizes = [len(macro.members) for macro in level.macros]
        levels.append(
            {
                "level": index,
                "macro_count": len(level.macros),
                "minimum_macro_members": min(sizes, default=0),
                "median_macro_members": median(sizes) if sizes else 0,
                "maximum_macro_members": max(sizes, default=0),
                "fixed_macros": sum(macro.fixed for macro in level.macros),
            }
        )

    print(
        json.dumps(
            {
                "benchmark": "snake-multilevel-coarsening",
                "implementation_combinators": result.physical_circuit.combinator_count,
                "physical_entities": result.physical_circuit.blueprint_entity_count,
                "logical_hyperedges": len(hierarchy.hyperedges),
                "maximum_hyperedge_members": max(
                    (len(edge.members) for edge in hierarchy.hyperedges),
                    default=0,
                ),
                "target_macros": args.target_macros,
                "levels": levels,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
