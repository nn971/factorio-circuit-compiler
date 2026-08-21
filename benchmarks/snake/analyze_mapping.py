"""Analyze Snake with the implementation-neutral temporal technology-mapping prototypes.

The default mode maps only Snake's post-update output cone. The caller prescribes a logical period;
the script never imports ``StateTimingPlan`` phases or an inferred period into the mapping problem.
``--extract-full-state`` instead stops after extracting phase-neutral register-read and state-transition
obligations, before any unsupported physical state-cell timing is invented.
"""

from __future__ import annotations

import argparse
from collections import Counter

from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    add_wire_sum_candidates,
    build_periodic_level_mapping_problem,
    build_periodic_state_mapping_problem,
    ordinary_candidates,
    solve_mapping_problem,
)
from factorio_circuit.sampling import SamplingPolicy

from .model import build_snake_circuit


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--period",
        required=True,
        type=int,
        help="caller-prescribed logical period in Factorio ticks",
    )
    parser.add_argument(
        "--max-delay-buses",
        type=int,
        default=1,
        help="maximum shared scalar delay buses in the joint solve (default: 1)",
    )
    parser.add_argument(
        "--bus-capacity",
        type=int,
        default=256,
        help="maximum scalar lanes assigned to one shared bus (default: 256)",
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=30.0,
        help="CP-SAT time limit in seconds (default: 30)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="CP-SAT worker count (default: 1 for deterministic diagnostics)",
    )
    parser.add_argument(
        "--without-wire-sum",
        action="store_true",
        help="disable the conservative zero-delay wire-sum alternatives",
    )
    parser.add_argument(
        "--without-framebuffer",
        action="store_true",
        help="omit the framebuffer output cone",
    )
    parser.add_argument(
        "--compare-private",
        action="store_true",
        help="also solve the same output-cone problem with shared delay buses disabled",
    )
    parser.add_argument(
        "--extract-full-state",
        action="store_true",
        help="extract the full phase-neutral recurrence graph and stop before solving",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.period < 1:
        raise SystemExit("--period must be positive")

    module = lower_frontend(
        build_snake_circuit(render_framebuffer=not args.without_framebuffer)
    )
    output_phase = 2 * args.period - 1
    output_phases = (output_phase,) * len(module.output.values)

    if args.extract_full_state:
        problem = build_periodic_state_mapping_problem(
            module,
            period=args.period,
            output_phases=output_phases,
            sampling_policy=SamplingPolicy.ALAP,
        )
        read_offsets = Counter(read.logical_offset for read in problem.state_reads)
        transition_kinds = Counter(item.kind for item in problem.state_transitions)
        transition_offsets = Counter(item.logical_offset for item in problem.state_transitions)
        print("Snake phase-neutral recurrence extraction")
        print(f"  prescribed_period={args.period}; output_phase={output_phase}")
        print(
            "  problem: "
            f"fixed_sources={len(problem.sources)}; state_reads={len(problem.state_reads)}; "
            f"operations={len(problem.operations)}; sinks={len(problem.sinks)}; "
            f"state_transitions={len(problem.state_transitions)}"
        )
        read_summary = ", ".join(
            f"offset{offset}:{count}" for offset, count in sorted(read_offsets.items())
        )
        transition_summary = ", ".join(
            f"{kind}:{count}" for kind, count in sorted(transition_kinds.items())
        )
        transition_offset_summary = ", ".join(
            f"offset{offset}:{count}" for offset, count in sorted(transition_offsets.items())
        )
        print(f"  state_read_occurrences={read_summary or 'none'}")
        print(f"  transition_kinds={transition_summary or 'none'}")
        print(f"  transition_occurrences={transition_offset_summary or 'none'}")
        print("  solve=not attempted; physical state-cell port timing is intentionally unresolved")
        return

    problem = build_periodic_level_mapping_problem(
        module,
        period=args.period,
        output_phases=output_phases,
        sampling_policy=SamplingPolicy.ALAP,
    )
    candidates = ordinary_candidates(problem)
    if not args.without_wire_sum:
        candidates = add_wire_sum_candidates(problem, candidates)

    result = solve_mapping_problem(
        problem,
        candidates=candidates,
        max_delay_buses=args.max_delay_buses,
        delay_bus_capacity=args.bus_capacity,
        time_limit_seconds=args.time_limit,
        workers=args.workers,
    )

    register_offsets = Counter(
        source.semantic.offset
        for source in problem.sources
        if isinstance(source.semantic, VectorRegisterRead)
    )
    selected = {item.operation: item.candidate for item in result.plan.realizations}
    candidate_by_id = {item.id: item for item in candidates}
    kinds = Counter(candidate_by_id[candidate_id].kind.value for candidate_id in selected.values())

    print("Snake periodic output-cone mapping")
    print(f"  prescribed_period={args.period}; output_phase={output_phase}")
    print(
        "  problem: "
        f"sources={len(problem.sources)}; operations={len(problem.operations)}; "
        f"uses={len(problem.uses())}; sinks={len(problem.sinks)}"
    )
    occurrence_summary = ", ".join(
        f"offset{offset}:{count}" for offset, count in sorted(register_offsets.items())
    )
    print(f"  register_occurrences={occurrence_summary or 'none'}")
    print(
        f"  solve: status={result.status}; wall={result.wall_time_seconds:.3f}s; "
        f"entity_cost={result.plan.entity_cost}; transport_cost={result.plan.transport_cost}; "
        f"total={result.plan.total_cost}"
    )
    candidate_summary = ", ".join(
        f"{kind}:{count}" for kind, count in sorted(kinds.items())
    )
    print(f"  selected_candidates={candidate_summary or 'none'}")
    print(f"  delay_buses={len(result.plan.delay_buses)}")
    for bus in result.plan.delay_buses:
        print(
            f"    bus {bus.index}: middle=[{bus.middle_start_phase}, "
            f"{bus.middle_end_phase}); stages={bus.middle_stages}; "
            f"interfaces={bus.interface_combinators}; lanes={len(bus.lanes)}"
        )

    if args.compare_private:
        private = solve_mapping_problem(
            problem,
            candidates=candidates,
            max_delay_buses=0,
            time_limit_seconds=args.time_limit,
            workers=args.workers,
        )
        print(
            "  all-private comparison: "
            f"status={private.status}; entity_cost={private.plan.entity_cost}; "
            f"transport_cost={private.plan.transport_cost}; total={private.plan.total_cost}; "
            f"joint_delta={result.plan.total_cost - private.plan.total_cost:+d}"
        )


if __name__ == "__main__":
    main()
