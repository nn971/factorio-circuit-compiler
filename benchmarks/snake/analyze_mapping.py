"""Analyze Snake with the implementation-neutral temporal technology-mapping prototypes.

The default mode maps only Snake's post-update output cone. ``--extract-full-state`` traverses the
phase-neutral recurrence and stops. ``--solve-full-state`` supplies clocked Freeze/Accumulator
state-cell candidates plus the shared periodic commit resource and jointly solves state phases,
target computation timing, exact transport, and optional shared scalar delay buses, without
consulting ``StateTimingPlan``.
"""

from __future__ import annotations

import argparse
from collections import Counter

from factorio_circuit.ir.state import VectorRegisterRead
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    add_select_constant_candidates,
    add_wire_sum_candidates,
    build_periodic_level_mapping_problem,
    build_periodic_state_mapping_problem,
    ordinary_candidates,
    ordinary_state_candidates,
    solve_mapping_problem,
    solve_periodic_state_bus_mapping_problem,
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
        help="maximum shared scalar delay buses in the selected solve (default: 1)",
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
        help="disable conservative zero-delay wire-sum alternatives in output-cone mode",
    )
    parser.add_argument(
        "--without-framebuffer",
        action="store_true",
        help="omit the framebuffer output cone",
    )
    parser.add_argument(
        "--compare-private",
        action="store_true",
        help="also solve the same problem with shared delay buses disabled",
    )
    state_mode = parser.add_mutually_exclusive_group()
    state_mode.add_argument(
        "--extract-full-state",
        action="store_true",
        help="extract the full phase-neutral recurrence graph and stop before solving",
    )
    state_mode.add_argument(
        "--solve-full-state",
        action="store_true",
        help="jointly solve full recurrence timing with clocked state cells and delay buses",
    )
    return parser


def _full_state_problem(module, *, period: int, output_phase: int):
    return build_periodic_state_mapping_problem(
        module,
        period=period,
        output_phases=(output_phase,) * len(module.output.values),
        sampling_policy=SamplingPolicy.ALAP,
    )


def _print_full_state_extraction(problem, *, period: int, output_phase: int) -> None:
    read_offsets = Counter(read.logical_offset for read in problem.state_reads)
    transition_kinds = Counter(item.kind for item in problem.state_transitions)
    transition_offsets = Counter(item.logical_offset for item in problem.state_transitions)
    print("Snake phase-neutral recurrence extraction")
    print(f"  prescribed_period={period}; output_phase={output_phase}")
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


def _solve_full_state(
    problem,
    *,
    max_delay_buses: int,
    bus_capacity: int,
    compare_private: bool,
    time_limit: float,
    workers: int,
) -> None:
    operation_candidates = add_select_constant_candidates(problem, ordinary_candidates(problem))
    state_candidates = ordinary_state_candidates(problem)
    result = solve_periodic_state_bus_mapping_problem(
        problem,
        candidates=operation_candidates,
        state_candidates=state_candidates,
        max_delay_buses=max_delay_buses,
        delay_bus_capacity=bus_capacity,
        time_limit_seconds=time_limit,
        workers=workers,
    )

    state_candidate_by_id = {item.id: item for item in state_candidates}
    operation_candidate_by_id = {item.id: item for item in operation_candidates}
    operation_entity_cost = sum(item.entity_cost for item in result.plan.realizations)
    state_entity_cost = sum(item.entity_cost for item in result.plan.state_cells)
    commit_entity_cost = (
        0 if result.plan.periodic_commit is None else result.plan.periodic_commit.entity_cost
    )
    delivery_kinds = Counter(item.kind.value for item in result.plan.deliveries)
    selected_recipes = Counter(
        operation_candidate_by_id[item.candidate].recipe.value for item in result.plan.realizations
    )

    print("Snake full recurrence mapping")
    print(f"  prescribed_period={problem.period}; horizon={problem.horizon}")
    print(
        "  problem: "
        f"fixed_sources={len(problem.sources)}; state_reads={len(problem.state_reads)}; "
        f"operations={len(problem.operations)}; sinks={len(problem.sinks)}; "
        f"state_transitions={len(problem.state_transitions)}"
    )
    print(
        f"  solve: status={result.status}; wall={result.wall_time_seconds:.3f}s; "
        f"operation_entities={operation_entity_cost}; state_entities={state_entity_cost}; "
        f"commit_entities={commit_entity_cost}; transport={result.plan.transport_cost}; "
        f"total={result.plan.total_cost}"
    )
    recipe_summary = ", ".join(
        f"{recipe}:{count}" for recipe, count in sorted(selected_recipes.items())
    )
    print(f"  selected_recipes={recipe_summary or 'none'}")
    if result.plan.periodic_commit is not None:
        print(
            "  periodic_commit: "
            f"period={result.plan.periodic_commit.period}; "
            f"ready_phase={result.plan.periodic_commit.ready_phase}; "
            f"entities={result.plan.periodic_commit.entity_cost}"
        )
    delivery_summary = ", ".join(
        f"{kind}:{count}" for kind, count in sorted(delivery_kinds.items())
    )
    print(f"  deliveries={delivery_summary or 'none'}")
    print(f"  exact_lifetimes={len(result.plan.exact_lifetimes)}")
    print(f"  delay_buses={len(result.plan.delay_buses)}")
    for bus in result.plan.delay_buses:
        print(
            f"    bus {bus.index}: middle=[{bus.middle_start_phase}, "
            f"{bus.middle_end_phase}); stages={bus.middle_stages}; "
            f"interfaces={bus.interface_combinators}; lanes={len(bus.lanes)}"
        )
    print("  selected_state_cells:")
    for cell in sorted(result.plan.state_cells, key=lambda item: item.register_name):
        candidate = state_candidate_by_id[cell.candidate]
        print(
            f"    {cell.register_name}: {candidate.name}; "
            f"base_read_phase={cell.base_read_phase}; entities={cell.entity_cost}"
        )

    if compare_private:
        private = solve_periodic_state_bus_mapping_problem(
            problem,
            candidates=operation_candidates,
            state_candidates=state_candidates,
            max_delay_buses=0,
            delay_bus_capacity=bus_capacity,
            time_limit_seconds=time_limit,
            workers=workers,
        )
        print(
            "  all-private comparison: "
            f"status={private.status}; transport={private.plan.transport_cost}; "
            f"total={private.plan.total_cost}; "
            f"joint_delta={result.plan.total_cost - private.plan.total_cost:+d}"
        )


def main() -> None:
    args = _parser().parse_args()
    if args.period < 1:
        raise SystemExit("--period must be positive")

    module = lower_frontend(build_snake_circuit(render_framebuffer=not args.without_framebuffer))
    output_phase = 2 * args.period - 1
    output_phases = (output_phase,) * len(module.output.values)

    if args.extract_full_state or args.solve_full_state:
        problem = _full_state_problem(module, period=args.period, output_phase=output_phase)
        if args.extract_full_state:
            _print_full_state_extraction(
                problem,
                period=args.period,
                output_phase=output_phase,
            )
            print(
                "  solve=not attempted; physical state-cell port timing is intentionally unresolved"
            )
            return
        _solve_full_state(
            problem,
            max_delay_buses=args.max_delay_buses,
            bus_capacity=args.bus_capacity,
            compare_private=args.compare_private,
            time_limit=args.time_limit,
            workers=args.workers,
        )
        return

    problem = build_periodic_level_mapping_problem(
        module,
        period=args.period,
        output_phases=output_phases,
        sampling_policy=SamplingPolicy.ALAP,
    )
    candidates = add_select_constant_candidates(problem, ordinary_candidates(problem))
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
    recipes = Counter(
        candidate_by_id[candidate_id].recipe.value for candidate_id in selected.values()
    )

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
    candidate_summary = ", ".join(f"{kind}:{count}" for kind, count in sorted(kinds.items()))
    recipe_summary = ", ".join(f"{recipe}:{count}" for recipe, count in sorted(recipes.items()))
    print(f"  selected_candidates={candidate_summary or 'none'}")
    print(f"  selected_recipes={recipe_summary or 'none'}")
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
