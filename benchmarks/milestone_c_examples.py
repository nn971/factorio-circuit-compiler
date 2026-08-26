"""Export before/after Milestone C layout examples as self-contained SVG files."""

from __future__ import annotations

import argparse
from dataclasses import replace
from html import escape
import json
from pathlib import Path

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
from factorio_circuit.ir.physical import WireColor
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


def _bounds(layout: Layout) -> tuple[float, float, float, float]:
    relay_ids = {relay.entity_id for relay in layout.relays}
    entities = {entity.id: entity for entity in layout.circuit.entities}
    left = float("inf")
    right = float("-inf")
    top = float("inf")
    bottom = float("-inf")
    for entity_id, (x, y) in layout.positions.items():
        half_x, half_y = (
            (0.5, 0.5)
            if entity_id in relay_ids
            else base_placement._entity_half_extent(entities[entity_id])
        )
        left = min(left, x - half_x)
        right = max(right, x + half_x)
        top = min(top, y - half_y)
        bottom = max(bottom, y + half_y)
    if not layout.positions:
        return (0.0, 1.0, 0.0, 1.0)
    return left, right, top, bottom


def _svg(layout: Layout, problem: LayoutOptimizationProblem, *, title: str) -> str:
    scale = 24.0
    margin = 24.0
    left, right, top, bottom = _bounds(layout)
    width = max(1.0, right - left) * scale + 2 * margin
    height = max(1.0, bottom - top) * scale + 2 * margin
    relay_ids = {relay.entity_id for relay in layout.relays}
    entities = {entity.id: entity for entity in layout.circuit.entities}
    fixed = set(problem.fixed_positions)

    def point(position: tuple[float, float]) -> tuple[float, float]:
        return (
            margin + (position[0] - left) * scale,
            margin + (position[1] - top) * scale,
        )

    rows = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.3f} {height:.3f}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<title>{escape(title)}</title>',
    ]

    for wire in layout.wires:
        x1, y1 = point(layout.positions[wire.source_entity])
        x2, y2 = point(layout.positions[wire.target_entity])
        stroke = "#c62828" if wire.color == WireColor.RED else "#2e7d32"
        rows.append(
            f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
            f'stroke="{stroke}" stroke-width="2" stroke-opacity="0.75"/>'
        )

    show_labels = len(layout.positions) <= 100
    for entity_id, position in sorted(layout.positions.items()):
        cx, cy = point(position)
        if entity_id in relay_ids:
            rows.append(
                f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{0.32 * scale:.3f}" '
                'fill="#bdbdbd" stroke="#424242" stroke-width="1.5"/>'
            )
        else:
            half_x, half_y = base_placement._entity_half_extent(entities[entity_id])
            x = cx - half_x * scale
            y = cy - half_y * scale
            stroke = "#1565c0" if entity_id in fixed else "#424242"
            stroke_width = 3 if entity_id in fixed else 1.5
            rows.append(
                f'<rect x="{x:.3f}" y="{y:.3f}" width="{2 * half_x * scale:.3f}" '
                f'height="{2 * half_y * scale:.3f}" fill="#fafafa" stroke="{stroke}" '
                f'stroke-width="{stroke_width}"/>'
            )
        if show_labels:
            rows.append(
                f'<text x="{cx:.3f}" y="{cy + 3.5:.3f}" text-anchor="middle" '
                'font-family="monospace" font-size="9" fill="#111">'
                f'{entity_id}</text>'
            )

    rows.append("</svg>")
    return "\n".join(rows) + "\n"


def _objective(layout: Layout) -> list[float]:
    return list(physical_layout_metrics(layout).objective)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="directory to write SVGs and index.html")
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--include-scale", action="store_true")
    args = parser.parse_args()
    if args.proposals < 0:
        parser.error("--proposals must be non-negative")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = list(CASES)
    if args.include_scale:
        cases.append(("large-sparse-1200", _large_sparse_case))

    manifest: list[dict[str, object]] = []
    html_rows = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Milestone C layout examples</title>",
        "<style>body{font-family:sans-serif;max-width:1200px;margin:2rem auto} "
        ".pair{display:grid;grid-template-columns:1fr 1fr;gap:1rem} "
        "img{max-width:100%;border:1px solid #ccc}</style>",
        "<h1>Milestone C layout examples</h1>",
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
        optimized = optimize_physical_layout(case.problem, options=options)
        validate_physical_layout(replace(case.problem, layout=optimized.layout))
        before_file = f"{name}-before.svg"
        after_file = f"{name}-after.svg"
        before_objective = _objective(case.problem.layout)
        after_objective = _objective(optimized.layout)
        (output / before_file).write_text(
            _svg(case.problem.layout, case.problem, title=f"{name}: before")
        )
        (output / after_file).write_text(
            _svg(optimized.layout, case.problem, title=f"{name}: after")
        )
        manifest.append(
            {
                "case": name,
                "seed": args.seed,
                "proposals": args.proposals,
                "before": before_objective,
                "after": after_objective,
                "before_svg": before_file,
                "after_svg": after_file,
            }
        )
        html_rows.extend(
            [
                f"<h2>{escape(name)}</h2>",
                f"<p>objective: {escape(str(before_objective))} → "
                f"{escape(str(after_objective))}</p>",
                "<div class='pair'>",
                f"<figure><figcaption>before</figcaption><img src='{before_file}'></figure>",
                f"<figure><figcaption>after</figcaption><img src='{after_file}'></figure>",
                "</div>",
            ]
        )

    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "index.html").write_text("\n".join(html_rows) + "\n")
    print(f"wrote {len(manifest)} example pairs to {output}")


if __name__ == "__main__":
    main()
