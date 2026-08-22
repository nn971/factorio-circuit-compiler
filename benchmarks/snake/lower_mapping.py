"""Solve and physically lower Snake through the periodic technology mapper.

This diagnostic is intentionally separate from the production compiler. It takes the same full
phase-neutral recurrence used by ``analyze_mapping --solve-full-state``, solves ordinary computation
and clocked state-cell timing, then lowers that exact :class:`RealizationPlan` to Abstract Physical
IR without invoking the established state-timing analyzer.

The report distinguishes the solver objective from hardware that the current mapper deliberately
does not price yet: fixed semantic constant sources, candidate-internal Select preservation, and
coherent dense output-boundary materialization. ``unexplained_gap == 0`` is the important checkpoint
before mapped physical synthesis is trusted.
"""

from __future__ import annotations

import argparse

from factorio_circuit.ir.abstract_physical import ConstantCombinator
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    build_periodic_state_mapping_problem,
    lower_periodic_state_mapping_plan,
    ordinary_candidates,
    ordinary_state_candidates,
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
        help="maximum shared scalar delay buses (default: 1)",
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
        "--without-framebuffer",
        action="store_true",
        help="omit the framebuffer output cone",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.period < 3:
        raise SystemExit("--period must be at least 3 for the first clocked commit resource")

    module = lower_frontend(build_snake_circuit(render_framebuffer=not args.without_framebuffer))
    output_phase = 2 * args.period - 1
    problem = build_periodic_state_mapping_problem(
        module,
        period=args.period,
        output_phases=(output_phase,) * len(module.output.values),
        sampling_policy=SamplingPolicy.ALAP,
    )
    operation_candidates = ordinary_candidates(problem)
    state_candidates = ordinary_state_candidates(problem)
    solve = solve_periodic_state_bus_mapping_problem(
        problem,
        candidates=operation_candidates,
        state_candidates=state_candidates,
        max_delay_buses=args.max_delay_buses,
        delay_bus_capacity=args.bus_capacity,
        time_limit_seconds=args.time_limit,
        workers=args.workers,
    )
    lowered = lower_periodic_state_mapping_plan(
        module,
        problem,
        operation_candidates,
        state_candidates,
        solve.plan,
    )

    operation_entities = sum(item.entity_cost for item in solve.plan.realizations)
    state_entities = sum(item.entity_cost for item in solve.plan.state_cells)
    commit_entities = (
        0 if solve.plan.periodic_commit is None else solve.plan.periodic_commit.entity_cost
    )
    implementation_entities = [
        entity
        for entity in lowered.circuit.entities
        if not (isinstance(entity, ConstantCombinator) and entity.annotation_only)
    ]
    annotation_entities = len(lowered.circuit.entities) - len(implementation_entities)

    print("Snake mapped physical lowering")
    print(
        f"  prescribed_period={args.period}; output_phase={output_phase}; "
        f"solve_status={solve.status}; wall={solve.wall_time_seconds:.3f}s"
    )
    print(
        "  mapped_problem: "
        f"fixed_sources={len(problem.sources)}; state_reads={len(problem.state_reads)}; "
        f"operations={len(problem.operations)}; sinks={len(problem.sinks)}; "
        f"state_transitions={len(problem.state_transitions)}"
    )
    print(
        "  plan: "
        f"operation_entities={operation_entities}; state_entities={state_entities}; "
        f"commit_entities={commit_entities}; transport={solve.plan.transport_cost}; "
        f"total={solve.plan.total_cost}"
    )
    print(
        "  lowering_cost: "
        f"planned={lowered.planned_cost}; fixed_sources={lowered.fixed_source_entities}; "
        f"candidate_internal={lowered.candidate_internal_entities}; "
        f"output_materialization={lowered.output_materialization_entities}; "
        f"accounted={lowered.accounted_cost}; emitted={lowered.emitted_combinators}; "
        f"unexplained_gap={lowered.unexplained_cost_gap:+d}"
    )
    print(
        "  abstract_physical: "
        f"implementation_entities={len(implementation_entities)}; "
        f"annotation_entities={annotation_entities}; nets={len(lowered.circuit.nets)}; "
        f"signals={len(lowered.circuit.signals)}; inputs={len(lowered.circuit.inputs)}; "
        f"outputs={len(lowered.circuit.outputs)}"
    )
    print(
        "  cost_exact_after_known_surcharges="
        f"{'yes' if lowered.cost_exact_after_known_surcharges else 'NO'}"
    )
    print(f"  delay_buses={len(solve.plan.delay_buses)}")
    for bus in solve.plan.delay_buses:
        print(
            f"    bus {bus.index}: middle=[{bus.middle_start_phase}, "
            f"{bus.middle_end_phase}); stages={bus.middle_stages}; "
            f"interfaces={bus.interface_combinators}; lanes={len(bus.lanes)}"
        )


if __name__ == "__main__":
    main()
