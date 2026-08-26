"""Export initial/pre-C/current Milestone C layout examples as self-contained SVG files."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from html import escape
from pathlib import Path
from typing import Any

from benchmarks.layout_optimizer_corpus import (
    _narrow_corridor_case,
    _perimeter_anchor_case,
    _relay_forest_case,
)
from benchmarks.layout_optimizer_topology_corpus import (
    _clustered_sparse_cut_case,
    _large_sparse_case,
    _red_green_mesh_case,
)
from benchmarks.milestone_c_acceptance import (
    PRE_C_BASELINE,
    _ensure_baseline,
    _git,
    _repo_root,
)
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    optimize_physical_layout,
    physical_layout_metrics,
    validate_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions

CASES = (
    ("relay-forest", _relay_forest_case),
    ("clustered-sparse-cut", _clustered_sparse_cut_case),
    ("red-green-mesh", _red_green_mesh_case),
    ("narrow-corridor", _narrow_corridor_case),
    ("perimeter-anchor", _perimeter_anchor_case),
)

_BASELINE_WORKER = r"""
import json
import sys
from dataclasses import replace

from benchmarks.layout_optimizer_corpus import (
    _narrow_corridor_case,
    _perimeter_anchor_case,
    _relay_forest_case,
)
from benchmarks.layout_optimizer_topology_corpus import (
    _clustered_sparse_cut_case,
    _large_sparse_case,
    _red_green_mesh_case,
)
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.layout_optimizer import (
    optimize_physical_layout,
    physical_layout_metrics,
    validate_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions

factories = {
    "relay-forest": _relay_forest_case,
    "clustered-sparse-cut": _clustered_sparse_cut_case,
    "red-green-mesh": _red_green_mesh_case,
    "narrow-corridor": _narrow_corridor_case,
    "perimeter-anchor": _perimeter_anchor_case,
    "large-sparse-1200": _large_sparse_case,
}


def snapshot(layout, problem):
    relay_ids = {relay.entity_id for relay in layout.relays}
    entities = {entity.id: entity for entity in layout.circuit.entities}
    footprints = {}
    for entity_id in layout.positions:
        if entity_id in relay_ids:
            footprints[str(entity_id)] = [0.5, 0.5]
        else:
            footprints[str(entity_id)] = list(
                base_placement._entity_half_extent(entities[entity_id])
            )
    return {
        "objective": list(physical_layout_metrics(layout).objective),
        "positions": {str(key): list(value) for key, value in layout.positions.items()},
        "relay_ids": sorted(relay_ids),
        "footprints": footprints,
        "fixed_ids": sorted(problem.fixed_positions),
        "wires": [
            {
                "source": wire.source_entity,
                "target": wire.target_entity,
                "color": wire.color.name.lower(),
            }
            for wire in layout.wires
        ],
    }


request = json.loads(sys.stdin.read())
rows = {}
for item in request:
    case = factories[item["case"]]()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=item["proposals"],
        random_seed=item["seed"],
        restarts=1,
    )
    optimized = optimize_physical_layout(case.problem, options=options)
    validate_physical_layout(replace(case.problem, layout=optimized.layout))
    rows[item["case"]] = snapshot(optimized.layout, case.problem)

print(json.dumps(rows, separators=(",", ":")))
"""


def _snapshot(layout: Layout, problem: LayoutOptimizationProblem) -> dict[str, Any]:
    relay_ids = {relay.entity_id for relay in layout.relays}
    entities = {entity.id: entity for entity in layout.circuit.entities}
    footprints: dict[str, list[float]] = {}
    for entity_id in layout.positions:
        if entity_id in relay_ids:
            footprints[str(entity_id)] = [0.5, 0.5]
        else:
            footprints[str(entity_id)] = list(
                base_placement._entity_half_extent(entities[entity_id])
            )
    return {
        "objective": list(physical_layout_metrics(layout).objective),
        "positions": {str(key): list(value) for key, value in layout.positions.items()},
        "relay_ids": sorted(relay_ids),
        "footprints": footprints,
        "fixed_ids": sorted(problem.fixed_positions),
        "wires": [
            {
                "source": wire.source_entity,
                "target": wire.target_entity,
                "color": wire.color.name.lower(),
            }
            for wire in layout.wires
        ],
    }


def _bounds(snapshot: dict[str, Any]) -> tuple[float, float, float, float]:
    positions = snapshot["positions"]
    if not positions:
        return (0.0, 1.0, 0.0, 1.0)
    left = float("inf")
    right = float("-inf")
    top = float("inf")
    bottom = float("-inf")
    footprints = snapshot["footprints"]
    for entity_id, position in positions.items():
        x, y = position
        half_x, half_y = footprints[entity_id]
        left = min(left, x - half_x)
        right = max(right, x + half_x)
        top = min(top, y - half_y)
        bottom = max(bottom, y + half_y)
    return left, right, top, bottom


def _svg(snapshot: dict[str, Any], *, title: str) -> str:
    scale = 24.0
    margin = 24.0
    left, right, top, bottom = _bounds(snapshot)
    width = max(1.0, right - left) * scale + 2 * margin
    height = max(1.0, bottom - top) * scale + 2 * margin
    relay_ids = set(snapshot["relay_ids"])
    fixed_ids = set(snapshot["fixed_ids"])
    positions = snapshot["positions"]
    footprints = snapshot["footprints"]

    def point(position: list[float]) -> tuple[float, float]:
        return (
            margin + (position[0] - left) * scale,
            margin + (position[1] - top) * scale,
        )

    rows = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.3f} {height:.3f}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f"<title>{escape(title)}</title>",
    ]

    for wire in snapshot["wires"]:
        x1, y1 = point(positions[str(wire["source"])])
        x2, y2 = point(positions[str(wire["target"])])
        stroke = "#c62828" if wire["color"] == "red" else "#2e7d32"
        rows.append(
            f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
            f'stroke="{stroke}" stroke-width="2" stroke-opacity="0.75"/>'
        )

    show_labels = len(positions) <= 100
    for entity_id_text, position in sorted(positions.items(), key=lambda item: int(item[0])):
        entity_id = int(entity_id_text)
        cx, cy = point(position)
        if entity_id in relay_ids:
            rows.append(
                f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{0.32 * scale:.3f}" '
                'fill="#bdbdbd" stroke="#424242" stroke-width="1.5"/>'
            )
        else:
            half_x, half_y = footprints[entity_id_text]
            x = cx - half_x * scale
            y = cy - half_y * scale
            stroke = "#1565c0" if entity_id in fixed_ids else "#424242"
            stroke_width = 3 if entity_id in fixed_ids else 1.5
            rows.append(
                f'<rect x="{x:.3f}" y="{y:.3f}" width="{2 * half_x * scale:.3f}" '
                f'height="{2 * half_y * scale:.3f}" fill="#fafafa" stroke="{stroke}" '
                f'stroke-width="{stroke_width}"/>'
            )
        if show_labels:
            rows.append(
                f'<text x="{cx:.3f}" y="{cy + 3.5:.3f}" text-anchor="middle" '
                'font-family="monospace" font-size="9" fill="#111">'
                f"{entity_id}</text>"
            )

    rows.append("</svg>")
    return "\n".join(rows) + "\n"


def _run_baseline(
    tree: Path,
    request: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = os.pathsep.join((str(tree / "src"), str(tree)))
    completed = subprocess.run(
        [sys.executable, "-c", _BASELINE_WORKER],
        cwd=tree,
        env=env,
        input=json.dumps(request),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _outcome(baseline: list[float], current: list[float]) -> str:
    if tuple(current) < tuple(baseline):
        return "better"
    if tuple(current) > tuple(baseline):
        return "worse"
    return "equal"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="directory to write SVGs and index.html")
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--include-scale", action="store_true")
    args = parser.parse_args()
    if args.proposals < 0:
        parser.error("--proposals must be non-negative")

    root = _repo_root()
    _ensure_baseline(root)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    cases = list(CASES)
    if args.include_scale:
        cases.append(("large-sparse-1200", _large_sparse_case))
    request = [
        {"case": name, "proposals": args.proposals, "seed": args.seed}
        for name, _factory in cases
    ]

    with tempfile.TemporaryDirectory(prefix="factorio-milestone-c-examples-") as temporary:
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
            baseline_snapshots = _run_baseline(baseline_tree, request)
        finally:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "remove", "--force", str(baseline_tree)],
                check=False,
                text=True,
                capture_output=True,
            )

    manifest: list[dict[str, Any]] = []
    html_rows = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Milestone C layout examples</title>",
        "<style>body{font-family:sans-serif;max-width:1600px;margin:2rem auto} "
        ".triple{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem} "
        "img{max-width:100%;border:1px solid #ccc} code{font-size:.9em}</style>",
        "<h1>Milestone C layout examples</h1>",
        f"<p>Frozen pre-C baseline: <code>{PRE_C_BASELINE}</code></p>",
    ]
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=args.proposals,
        random_seed=args.seed,
        restarts=1,
    )

    for name, factory in cases:
        case = factory()
        validate_physical_layout(case.problem)
        initial = _snapshot(case.problem.layout, case.problem)
        optimized = optimize_physical_layout(case.problem, options=options)
        validate_physical_layout(replace(case.problem, layout=optimized.layout))
        current = _snapshot(optimized.layout, case.problem)
        baseline = baseline_snapshots[name]

        files = {
            "initial": f"{name}-initial.svg",
            "pre_c": f"{name}-pre-c.svg",
            "current": f"{name}-current.svg",
        }
        for label, snapshot in (
            ("initial", initial),
            ("pre_c", baseline),
            ("current", current),
        ):
            (output / files[label]).write_text(
                _svg(snapshot, title=f"{name}: {label.replace('_', ' ')}")
            )

        outcome = _outcome(baseline["objective"], current["objective"])
        manifest.append(
            {
                "case": name,
                "seed": args.seed,
                "proposals": args.proposals,
                "pre_c_baseline": PRE_C_BASELINE,
                "outcome": outcome,
                "initial": initial["objective"],
                "pre_c": baseline["objective"],
                "current": current["objective"],
                "svg": files,
            }
        )
        html_rows.extend(
            [
                f"<h2>{escape(name)} — C vs pre-C: {outcome}</h2>",
                f"<p>initial <code>{escape(str(initial['objective']))}</code>; "
                f"pre-C <code>{escape(str(baseline['objective']))}</code>; "
                f"current <code>{escape(str(current['objective']))}</code></p>",
                "<div class='triple'>",
                f"<figure><figcaption>initial</figcaption><img src='{files['initial']}'></figure>",
                "<figure><figcaption>pre-C optimized</figcaption>"
                f"<img src='{files['pre_c']}'></figure>",
                "<figure><figcaption>current optimized</figcaption>"
                f"<img src='{files['current']}'></figure>",
                "</div>",
            ]
        )

    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "index.html").write_text("\n".join(html_rows) + "\n")
    print(f"wrote {len(manifest)} three-way example sets to {output}")


if __name__ == "__main__":
    main()
