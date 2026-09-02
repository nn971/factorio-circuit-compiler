"""Analyze 2048 with the accepted joint temporal technology mapper.

This diagnostic maps the full phase-neutral recurrence rather than the compatibility
``StateTimingPlan``/global sampling-policy route.  The caller supplies a candidate logical period;
operation implementations, physical phases, state-cell phases, exact delivery, wire sums, and
optional shared scalar delay buses are then selected jointly.

Framebuffer expansion is omitted by default so the first measurement describes the game recurrence
rather than the display decoder.  ``--extract-only`` requires no OR-Tools installation and is useful
for checking mapper graph size before attempting CP-SAT.
"""

from __future__ import annotations

import argparse
from collections import Counter
from time import monotonic

from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    MappingProblemError,
    add_decider_condition_cover_candidates,
    add_select_constant_candidates,
    add_wire_sum_candidates,
    build_periodic_state_mapping_problem,
    ordinary_candidates,
    ordinary_state_candidates,
    solve_periodic_state_bus_mapping_problem,
)
from factorio_circuit.sampling import SamplingPolicy

from .model import build_2048_circuit


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--period",
        required=True,
        type=int,
        help="candidate logical period P in Factorio ticks",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="build the full mapper problem and candidate set, but do not invoke CP-SAT",
    )
    parser.add_argument(
        "--with-framebuffer",
        action="store_true",
        help="include the 16x16 framebuffer expansion in the mapped output cone",
    )
    parser.add_argument(
        "--max-delay-buses",
        type=int,
        default=1,
        help="maximum shared scalar delay buses (default: 1)",
    )
    parser.add_argument(
        "--bus-capacity",
        type=int,
        default=256,
        help="maximum scalar lanes on one shared delay bus (default: 256)",
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=60.0,
        help="CP-SAT time limit in seconds (default: 60)",
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
        help="disable conservative zero-delay wire-sum candidates",
    )
    parser.add_argument(
        "--compare-private",
        action="store_true",
        help="also solve the same recurrence with shared delay buses disabled",
    )
    return parser


def _operation_candidates(problem, *, include_wire_sum: bool):
    candidates = ordinary_candidates(problem)
    candidates = add_select_constant_candidates(problem, candidates)
    candidates = add_decider_condition_cover_candidates(problem, candidates)
    if include_wire_sum:
        candidates = add_wire_sum_candidates(problem, candidates)
    return candidates


def _summary(counter: Counter[str]) -> str:
    return ", ".join(f"{key}:{value}" for key, value in sorted(counter.items())) or "none"


def main() -> None:
    args = _parser().parse_args()
    if args.period < 3:
        raise SystemExit("--period must be at least 3 for the current periodic state-cell ABI")

    started = monotonic()
    circuit = build_2048_circuit(render_framebuffer=args.with_framebuffer)
    print(f"model: {monotonic() - started:.3f}s")

    module = lower_frontend(circuit)
    print(f"frontend: {monotonic() - started:.3f}s")

    # The current extractor spells the live-input observation contract through SamplingPolicy.ALAP.
    # This does not impose a global ALAP schedule: it only marks external Level leaves OBSERVABLE so
    # the joint mapper may choose OBSERVE_AT deliveries together with all other physical decisions.
    output_phase = 2 * args.period - 1
    problem = build_periodic_state_mapping_problem(
        module,
        period=args.period,
        output_phases=(output_phase,) * len(module.output.values),
        sampling_policy=SamplingPolicy.ALAP,
    )
    print(f"extraction: {monotonic() - started:.3f}s")

    operation_candidates = _operation_candidates(
        problem,
        include_wire_sum=not args.without_wire_sum,
    )
    state_candidates = ordinary_state_candidates(problem)
    print(f"candidates: {monotonic() - started:.3f}s")

    operation_shapes = Counter(item.shape.value for item in problem.operations)
    operation_candidate_kinds = Counter(item.kind.value for item in operation_candidates)
    transition_kinds = Counter(item.kind for item in problem.state_transitions)
    read_offsets = Counter(str(item.logical_offset) for item in problem.state_reads)

    print("2048 full recurrence mapping problem")
    print(
        f"  period={args.period}; output_phase={output_phase}; horizon={problem.horizon}; "
        f"framebuffer={args.with_framebuffer}"
    )
    print(
        "  graph: "
        f"sources={len(problem.sources)}; state_reads={len(problem.state_reads)}; "
        f"operations={len(problem.operations)}; sinks={len(problem.sinks)}; "
        f"state_transitions={len(problem.state_transitions)}"
    )
    print(f"  operation_shapes={_summary(operation_shapes)}")
    print(f"  state_read_offsets={_summary(read_offsets)}")
    print(f"  transition_kinds={_summary(transition_kinds)}")
    print(
        "  candidates: "
        f"operation={len(operation_candidates)}; state={len(state_candidates)}; "
        f"kinds={_summary(operation_candidate_kinds)}"
    )

    if args.extract_only:
        return

    try:
        result = solve_periodic_state_bus_mapping_problem(
            problem,
            candidates=operation_candidates,
            state_candidates=state_candidates,
            max_delay_buses=args.max_delay_buses,
            delay_bus_capacity=args.bus_capacity,
            time_limit_seconds=args.time_limit,
            workers=args.workers,
        )
    except MappingProblemError as exc:
        raise SystemExit(f"mapping solve failed: {exc}") from exc

    candidate_by_id = {item.id: item for item in operation_candidates}
    selected_kinds = Counter(
        candidate_by_id[item.candidate].kind.value for item in result.plan.realizations
    )
    selected_recipes = Counter(
        candidate_by_id[item.candidate].recipe.value for item in result.plan.realizations
    )
    delivery_kinds = Counter(item.kind.value for item in result.plan.deliveries)
    operation_entity_cost = sum(item.entity_cost for item in result.plan.realizations)
    state_entity_cost = sum(item.entity_cost for item in result.plan.state_cells)

    print("2048 full recurrence mapping result")
    print(
        f"  solve: status={result.status}; wall={result.wall_time_seconds:.3f}s; "
        f"elapsed={monotonic() - started:.3f}s"
    )
    print(
        f"  cost: operation_entities={operation_entity_cost}; "
        f"state_entities={state_entity_cost}; transport={result.plan.transport_cost}; "
        f"total={result.plan.total_cost}"
    )
    print(f"  selected_candidates={_summary(selected_kinds)}")
    print(f"  selected_recipes={_summary(selected_recipes)}")
    print(f"  deliveries={_summary(delivery_kinds)}")
    print(
        f"  resources: wire_sums={len(result.plan.wire_sums)}; "
        f"exact_lifetimes={len(result.plan.exact_lifetimes)}; "
        f"delay_buses={len(result.plan.delay_buses)}"
    )
    for bus in result.plan.delay_buses:
        print(
            f"    bus {bus.index}: middle=[{bus.middle_start_phase}, "
            f"{bus.middle_end_phase}); stages={bus.middle_stages}; "
            f"interfaces={bus.interface_combinators}; lanes={len(bus.lanes)}"
        )
    print("  state_cells:")
    for cell in sorted(result.plan.state_cells, key=lambda item: item.register_name):
        print(
            f"    {cell.register_name}: base_read_phase={cell.base_read_phase}; "
            f"entities={cell.entity_cost}"
        )

    if args.compare_private:
        private_started = monotonic()
        try:
            private = solve_periodic_state_bus_mapping_problem(
                problem,
                candidates=operation_candidates,
                state_candidates=state_candidates,
                max_delay_buses=0,
                delay_bus_capacity=args.bus_capacity,
                time_limit_seconds=args.time_limit,
                workers=args.workers,
            )
        except MappingProblemError as exc:
            raise SystemExit(f"all-private comparison failed: {exc}") from exc
        print(
            "  all-private comparison: "
            f"status={private.status}; wall={private.wall_time_seconds:.3f}s; "
            f"elapsed={monotonic() - private_started:.3f}s; "
            f"transport={private.plan.transport_cost}; total={private.plan.total_cost}; "
            f"joint_delta={result.plan.total_cost - private.plan.total_cost:+d}"
        )


if __name__ == "__main__":
    main()
