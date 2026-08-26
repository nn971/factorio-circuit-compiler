"""User-verifiable acceptance gate for Milestone C (Annealing v2).

The command compares the current checkout with the frozen pre-Milestone-C commit in separate Python
processes. Heavy scale and budget-curve checks are opt-in via ``--full``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
import tempfile
from typing import Any

PRE_C_BASELINE = "a70df723768a6ba099ffd43017bdcb0291011c8f"
CORE_CASES = (
    "relay-forest",
    "shared-bus",
    "clustered-sparse-cut",
    "red-green-mesh",
    "near-optimal-packed",
    "narrow-corridor",
    "perimeter-anchor",
    "fixed-endpoint-span",
)
CURVE_CASES = (
    "relay-forest",
    "clustered-sparse-cut",
    "perimeter-anchor",
)
CURVE_BUDGETS = (256, 1024, 4096, 16384)
_OBJECTIVE_FIELDS = ("relay_count", "occupied_area", "wire_length")
_WORK_FIELDS = (
    "proposals",
    "accepted",
    "noop",
    "geometry",
    "reach",
    "metropolis",
    "routing_queue_pops",
    "topology_rebuilds",
)

_WORKER = r"""
import json
import sys
from dataclasses import replace
from time import perf_counter

from benchmarks.layout_optimizer_corpus import (
    _fixed_endpoint_span_case,
    _narrow_corridor_case,
    _perimeter_anchor_case,
    _relay_forest_case,
    _shared_bus_case,
)
from benchmarks.layout_optimizer_topology_corpus import (
    _clustered_sparse_cut_case,
    _large_sparse_case,
    _near_optimal_packed_case,
    _red_green_mesh_case,
)
from factorio_circuit.synthesis.layout_observability import optimize_physical_layout_observed
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions

factories = {
    "relay-forest": _relay_forest_case,
    "shared-bus": _shared_bus_case,
    "clustered-sparse-cut": _clustered_sparse_cut_case,
    "red-green-mesh": _red_green_mesh_case,
    "near-optimal-packed": _near_optimal_packed_case,
    "narrow-corridor": _narrow_corridor_case,
    "perimeter-anchor": _perimeter_anchor_case,
    "fixed-endpoint-span": _fixed_endpoint_span_case,
    "large-sparse-1200": _large_sparse_case,
}

request = json.loads(sys.stdin.read())
rows = []
for item in request:
    case = factories[item["case"]]()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=item["proposals"],
        random_seed=item["seed"],
        restarts=1,
    )
    started = perf_counter()
    observed = optimize_physical_layout_observed(case.problem, options=options)
    elapsed = perf_counter() - started
    validate_physical_layout(replace(case.problem, layout=observed.optimization.layout))
    stats = observed.stats
    rows.append(
        {
            **item,
            "objective": list(observed.optimization.after.objective),
            "runtime_seconds": elapsed,
            "work": {
                "proposals": stats.proposals_attempted,
                "accepted": stats.accepted_moves,
                "noop": stats.noop_rejections,
                "geometry": stats.geometry_rejections,
                "reach": stats.wire_reach_rejections,
                "metropolis": stats.metropolis_rejections,
                "routing_queue_pops": stats.routing_queue_pops,
                "topology_rebuilds": stats.topology_rebuild_attempts,
            },
        }
    )
print(json.dumps(rows, separators=(",", ":")))
"""


def _git(root: Path, *args: str, capture: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        text=True,
        capture_output=capture,
    )
    return completed.stdout.strip() if capture else ""


def _repo_root() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        text=True,
        capture_output=True,
    )
    return Path(completed.stdout.strip()).resolve()


def _ensure_baseline(root: Path) -> None:
    check = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{PRE_C_BASELINE}^{{commit}}"],
        text=True,
        capture_output=True,
    )
    if check.returncode != 0:
        raise SystemExit(
            "Frozen pre-C baseline is not available in this clone. Fetch commit "
            f"{PRE_C_BASELINE} explicitly (for example `git fetch origin {PRE_C_BASELINE}`) and retry."
        )


def _run_worker(tree: Path, request: list[dict[str, Any]]) -> list[dict[str, Any]]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = os.pathsep.join((str(tree / "src"), str(tree)))
    completed = subprocess.run(
        [sys.executable, "-c", _WORKER],
        cwd=tree,
        env=env,
        input=json.dumps(request),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _request(*, seeds: int, proposals: int, full: bool) -> list[dict[str, Any]]:
    rows = [
        {"case": case, "proposals": proposals, "seed": seed, "kind": "core"}
        for case in CORE_CASES
        for seed in range(seeds)
    ]
    if not full:
        return rows

    curve_seeds = min(4, seeds)
    seen = {(row["case"], row["proposals"], row["seed"]) for row in rows}
    for case in CURVE_CASES:
        for budget in CURVE_BUDGETS:
            for seed in range(curve_seeds):
                key = (case, budget, seed)
                if key in seen:
                    continue
                rows.append(
                    {"case": case, "proposals": budget, "seed": seed, "kind": "curve"}
                )
                seen.add(key)
    rows.append(
        {
            "case": "large-sparse-1200",
            "proposals": max(256, min(proposals, 4096)),
            "seed": 0,
            "kind": "scale",
        }
    )
    return rows


def _key(row: dict[str, Any]) -> tuple[str, int, int]:
    return (row["case"], row["proposals"], row["seed"])


def _fmt_objective(value: list[float]) -> str:
    return f"({int(value[0])}, {value[1]:.3f}, {value[2]:.3f})"


def _direction(new: Any, old: Any) -> str:
    if new < old:
        return "better"
    if new > old:
        return "worse"
    return "equal"


def _compare(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    before = {_key(row): row for row in baseline}
    after = {_key(row): row for row in current}
    if before.keys() != after.keys():
        raise RuntimeError("baseline/current worker result keys differ")

    lexicographic = {"better": 0, "equal": 0, "worse": 0}
    components = {
        field: {"better": 0, "equal": 0, "worse": 0} for field in _OBJECTIVE_FIELDS
    }
    runtime_ratios: list[float] = []
    rows: list[dict[str, Any]] = []

    print("case                     budget seed outcome  baseline -> current")
    print("-" * 104)
    for key in sorted(before):
        old = before[key]
        new = after[key]
        old_objective = tuple(old["objective"])
        new_objective = tuple(new["objective"])
        outcome = _direction(new_objective, old_objective)
        lexicographic[outcome] += 1

        component_outcomes: dict[str, str] = {}
        for index, field in enumerate(_OBJECTIVE_FIELDS):
            component_outcome = _direction(new_objective[index], old_objective[index])
            components[field][component_outcome] += 1
            component_outcomes[field] = component_outcome

        ratio = (
            new["runtime_seconds"] / old["runtime_seconds"]
            if old["runtime_seconds"]
            else 1.0
        )
        runtime_ratios.append(ratio)
        rows.append(
            {
                "case": key[0],
                "proposals": key[1],
                "seed": key[2],
                "outcome": outcome,
                "component_outcomes": component_outcomes,
                "baseline_objective": list(old_objective),
                "current_objective": list(new_objective),
                "runtime_ratio": ratio,
            }
        )
        print(
            f"{key[0]:24} {key[1]:6d} {key[2]:4d} {outcome:7}  "
            f"{_fmt_objective(old['objective'])} -> {_fmt_objective(new['objective'])}  "
            f"runtime x{ratio:.3f}"
        )

    median_runtime = median(runtime_ratios) if runtime_ratios else 1.0
    print()
    print(
        "OVERALL "
        f"better/equal/worse={lexicographic['better']}/"
        f"{lexicographic['equal']}/{lexicographic['worse']}; "
        f"median-runtime-ratio={median_runtime:.3f}"
    )
    print("Independent objective-component outcomes:")
    for field in _OBJECTIVE_FIELDS:
        counts = components[field]
        print(
            f"  {field:20} "
            f"{counts['better']}/{counts['equal']}/{counts['worse']} "
            "(better/equal/worse)"
        )

    work_delta = {
        field: sum(after[key]["work"][field] - before[key]["work"][field] for key in before)
        for field in _WORK_FIELDS
    }
    print("\nAggregated work deltas (current - pre-C):")
    for field in _WORK_FIELDS:
        print(f"  {field:20} {work_delta[field]:+d}")

    summary = {
        "lexicographic": lexicographic,
        "components": components,
        "median_runtime_ratio": median_runtime,
        "work_delta": work_delta,
        "rows": rows,
    }
    return lexicographic["worse"] == 0, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument(
        "--full",
        action="store_true",
        help="also run budget curves and the 1,200-object scale case",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="also write baseline/current rows and summary as JSON",
    )
    args = parser.parse_args()
    if args.seeds <= 0:
        parser.error("--seeds must be positive")
    if args.proposals <= 0:
        parser.error("--proposals must be positive")

    root = _repo_root()
    _ensure_baseline(root)
    request = _request(seeds=args.seeds, proposals=args.proposals, full=args.full)
    current_revision = _git(root, "rev-parse", "HEAD")
    print(f"pre-C baseline: {PRE_C_BASELINE}")
    print(f"current HEAD:   {current_revision}")
    print(f"runs per implementation: {len(request)}")

    with tempfile.TemporaryDirectory(prefix="factorio-milestone-c-") as temporary:
        baseline_tree = Path(temporary) / "baseline"
        _git(
            root,
            "worktree",
            "add",
            "--detach",
            str(baseline_tree),
            PRE_C_BASELINE,
            capture=False,
        )
        try:
            baseline = _run_worker(baseline_tree, request)
            current = _run_worker(root, request)
        finally:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "remove", "--force", str(baseline_tree)],
                check=False,
                text=True,
                capture_output=True,
            )

    passed, summary = _compare(baseline, current)
    if args.json_report is not None:
        report = {
            "pre_c_baseline": PRE_C_BASELINE,
            "current_revision": current_revision,
            "request": request,
            "baseline": baseline,
            "current": current,
            "summary": summary,
        }
        args.json_report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nwrote JSON report: {args.json_report}")

    if not passed:
        raise SystemExit("Milestone C acceptance failed: current optimizer lost a pre-C objective")


if __name__ == "__main__":
    main()
