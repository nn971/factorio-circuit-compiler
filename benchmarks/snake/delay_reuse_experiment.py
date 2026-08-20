"""Project Snake's eager phase-delay hardware to synthetic temporal holds.

This experiment deliberately leaves canonical lowering untouched. It first runs the
existing compiler through Abstract Physical IR so the eager delay count is exact, then
groups maximal one-token delay components. It asks how many could be replaced by one
capture/hold each inside the inferred periodic clock interval. No physical rewrite,
synthesis, layout, or blueprint is produced.
"""

from __future__ import annotations

import argparse
from time import monotonic

from benchmarks.snake.model import build_snake_circuit
from factorio_circuit import lower_to_abstract_physical
from factorio_circuit.analysis import census_abstract_physical
from factorio_circuit.experimental.delay_reuse import project_delay_reuse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-framebuffer",
        action="store_true",
        help="omit framebuffer and body-pixel state from the Snake workload",
    )
    args = parser.parse_args()

    started = monotonic()
    lowered = lower_to_abstract_physical(
        build_snake_circuit(render_framebuffer=not args.no_framebuffer),
        optimize=False,
    )
    lowered_at = monotonic()
    period = lowered.state_timing.uniform_period
    if period is None:
        raise ValueError("Snake delay-reuse experiment requires one uniform periodic state clock")

    census = census_abstract_physical(lowered.abstract_physical)
    projection = project_delay_reuse(lowered.abstract_physical, period=period)
    analyzed_at = monotonic()

    if projection.delay_count != census.phase_delay_entities:
        raise AssertionError(
            "delay-reuse projection and abstract-physical census disagree: "
            f"{projection.delay_count} != {census.phase_delay_entities}"
        )

    projected_total = (
        census.implementation_entities - projection.removable_delays + projection.projected_holds
    )
    print(projection.summary())
    print(
        "  projected implementation accounting: "
        f"non_delay={census.implementation_entities - census.phase_delay_entities}; "
        f"temporal_holds={projection.projected_holds}; "
        f"unreplaced_delays={projection.remaining_delays}; "
        f"total_if_one_entity_per_hold={projected_total}"
    )
    print(
        "  note: total_if_one_entity_per_hold is a structural projection, not yet an executable "
        "Factorio implementation; clock/capture hardware and future vector-bank sharing are omitted"
    )
    print(
        "  timings: "
        f"canonical_lowering={lowered_at - started:.1f}s; "
        f"projection={analyzed_at - lowered_at:.1f}s"
    )


if __name__ == "__main__":
    main()
