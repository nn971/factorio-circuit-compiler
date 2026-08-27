"""Paired trajectory/runtime check for deferred exact wire-length bookkeeping."""

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

BASE_SHA = "34c78d59ae9b4e2a07e5a76edc4dbb0ba356a16b"
CASES = (
    "relay-forest",
    "shared-bus",
    "clustered-sparse-cut",
    "red-green-mesh",
    "near-optimal-packed",
    "narrow-corridor",
    "perimeter-anchor",
    "fixed-endpoint-span",
)

_WORKER = r'''import json
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
    _near_optimal_packed_case,
    _red_green_mesh_case,
)
from factorio_circuit.synthesis.layout_optimizer import (
    optimize_physical_layout,
    validate_physical_layout,
)
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
    result = optimize_physical_layout(case.problem, options=options)
    elapsed = perf_counter() - started
    validate_physical_layout(replace(case.problem, layout=result.layout))
    rows.append(
        {
            **item,
            "runtime_seconds": elapsed,
            "objective": list(result.after.objective),
            "positions": sorted((key, list(value)) for key, value in result.layout.positions.items()),
            "relays": sorted(
                (relay.entity_id, list(relay.position), relay.description)
                for relay in result.layout.relays
            ),
            "wires": sorted(
                (
                    wire.source_entity,
                    wire.source_connector_id,
                    wire.target_entity,
                    wire.target_connector_id,
                    wire.color.value,
                )
                for wire in result.layout.wires
            ),
        }
    )
print(json.dumps(rows, separators=(",", ":")))
'''


def _repo_root() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        text=True,
        capture_output=True,
    )
    return Path(completed.stdout.strip()).resolve()


def _run_worker(tree: Path, request: list[dict[str, Any]]) -> list[dict[str, Any]]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = os.pathsep.join((str(tree / "src"), str(tree)))
    completed = subprocess.run(
        [sys.executable, "-c", _WORKER],
        cwd=tree,
        env=env,
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _fingerprint(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(row["objective"]),
        tuple((item[0], tuple(item[1])) for item in row["positions"]),
        tuple((item[0], tuple(item[1]), item[2]) for item in row["relays"]),
        tuple(tuple(item) for item in row["wires"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--max-median-ratio", type=float, default=0.98)
    parser.add_argument("--max-case-ratio", type=float, default=1.03)
    args = parser.parse_args()

    if args.seeds < 1 or args.proposals < 1:
        parser.error("--seeds and --proposals must be positive")

    root = _repo_root()
    subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{BASE_SHA}^{{commit}}"],
        check=True,
    )
    request = [
        {"case": case, "seed": seed, "proposals": args.proposals}
        for case in CASES
        for seed in range(args.seeds)
    ]

    with tempfile.TemporaryDirectory(prefix="factorio-c-exact-delta-") as temp:
        baseline_tree = Path(temp) / "baseline"
        subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "--detach", str(baseline_tree), BASE_SHA],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            baseline = _run_worker(baseline_tree, request)
            current = _run_worker(root, request)
        finally:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "remove", "--force", str(baseline_tree)],
                check=True,
                capture_output=True,
                text=True,
            )

    ratios: list[float] = []
    by_case: dict[str, list[float]] = {case: [] for case in CASES}
    for old, new in zip(baseline, current, strict=True):
        if (old["case"], old["seed"], old["proposals"]) != (
            new["case"],
            new["seed"],
            new["proposals"],
        ):
            raise RuntimeError("paired benchmark rows are misaligned")
        if _fingerprint(old) != _fingerprint(new):
            raise SystemExit(
                "trajectory changed for "
                f"{old['case']} seed={old['seed']} proposals={old['proposals']}"
            )
        ratio = new["runtime_seconds"] / old["runtime_seconds"]
        ratios.append(ratio)
        by_case[old["case"]].append(ratio)

    overall = median(ratios)
    print(f"identical layout fingerprints: {len(ratios)}/{len(ratios)}")
    print(f"overall median runtime ratio: {overall:.3f}x")
    for case in CASES:
        print(f"  {case:24s} {median(by_case[case]):.3f}x")

    if overall > args.max_median_ratio:
        raise SystemExit(
            f"median runtime ratio {overall:.3f} exceeds {args.max_median_ratio:.3f}"
        )
    slow_cases = [
        (case, median(values))
        for case, values in by_case.items()
        if median(values) > args.max_case_ratio
    ]
    if slow_cases:
        formatted = ", ".join(f"{case}={ratio:.3f}x" for case, ratio in slow_cases)
        raise SystemExit(f"case runtime regression exceeds limit: {formatted}")


if __name__ == "__main__":
    main()
