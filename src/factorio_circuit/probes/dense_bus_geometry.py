"""Generate tiny Factorio probes that isolate dense folded-bus geometry assumptions.

The full Snake compilation is intentionally avoided here.  Each probe contains two independent RED
circuit networks carried by blank constant-combinator relays.  Source A emits ``signal-A = 11`` and
source B emits ``signal-B = 22``.  The two labelled sink constants should see exactly their matching
signal and never the other one.

The four cases separate the two changes involved in the failed dense-bus experiment:

- ``control``: first offset 3.0, track spacing 2.0 (known-good folded geometry);
- ``half-offset``: first offset 3.5, track spacing 2.0 (tests mixed integer/half-tile rows only);
- ``unit-spacing``: first offset 3.0, track spacing 1.0 (tests adjacent 1x1 bus rows only);
- ``dense``: first offset 3.5, track spacing 1.0 (the exact failed combination).

Generate all four import strings with::

    uv run python -m factorio_circuit.probes.dense_bus_geometry --output-dir probe-blueprints
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint

FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113
RED_CONNECTOR: Final = 1


@dataclass(frozen=True, slots=True)
class ProbeCase:
    slug: str
    label: str
    first_bus_offset: float
    track_spacing: float


PROBE_CASES: Final[tuple[ProbeCase, ...]] = (
    ProbeCase(
        "control",
        "Dense bus probe — control offset=3 spacing=2",
        3.0,
        2.0,
    ),
    ProbeCase(
        "half-offset",
        "Dense bus probe — half offset=3.5 spacing=2",
        3.5,
        2.0,
    ),
    ProbeCase(
        "unit-spacing",
        "Dense bus probe — integer offset=3 spacing=1",
        3.0,
        1.0,
    ),
    ProbeCase(
        "dense",
        "Dense bus probe — failed candidate offset=3.5 spacing=1",
        3.5,
        1.0,
    ),
)


def _constant_behavior(signal_name: str, count: int) -> dict[str, object]:
    return {
        "sections": {
            "sections": [
                {
                    "index": 1,
                    "filters": [
                        {
                            "index": 1,
                            "type": "virtual",
                            "name": signal_name,
                            "quality": "normal",
                            "comparator": "=",
                            "count": count,
                        }
                    ],
                }
            ]
        }
    }


def _constant(
    entity_number: int,
    x: float,
    y: float,
    description: str,
    *,
    signal_name: str | None = None,
    count: int = 0,
) -> dict[str, object]:
    entity: dict[str, object] = {
        "entity_number": entity_number,
        "name": "constant-combinator",
        "position": {"x": x, "y": y},
        "player_description": description,
    }
    if signal_name is not None:
        entity["control_behavior"] = _constant_behavior(signal_name, count)
    return entity


def build_dense_bus_probe_blueprint(case: ProbeCase) -> Blueprint:
    """Build one 14-entity two-net probe for ``case``."""

    bus_a_y = -case.first_bus_offset
    bus_b_y = -(case.first_bus_offset + case.track_spacing)

    entities: list[dict[str, object]] = [
        _constant(1, 0.0, 0.0, "SOURCE A — emits signal-A = 11", signal_name="signal-A", count=11),
        _constant(2, 6.0, 0.0, "SOURCE B — emits signal-B = 22", signal_name="signal-B", count=22),
        _constant(3, 18.0, 0.0, "SINK A — EXPECT ONLY signal-A = 11"),
        _constant(4, 24.0, 0.0, "SINK B — EXPECT ONLY signal-B = 22"),
        # Network A: source feeder x=-2, horizontal relay lattice x=0 mod 6, sink feeder x=16.
        _constant(5, -2.0, bus_a_y, "A endpoint tap"),
        _constant(6, 0.0, bus_a_y, "A row bus relay"),
        _constant(7, 6.0, bus_a_y, "A row bus relay"),
        _constant(8, 12.0, bus_a_y, "A row bus relay"),
        _constant(9, 16.0, bus_a_y, "A endpoint tap"),
        # Network B: its feeder crosses A geometrically but has no A relay at the crossing.
        _constant(10, 4.0, bus_b_y, "B endpoint tap"),
        _constant(11, 6.0, bus_b_y, "B row bus relay"),
        _constant(12, 12.0, bus_b_y, "B row bus relay"),
        _constant(13, 18.0, bus_b_y, "B row bus relay"),
        _constant(14, 22.0, bus_b_y, "B endpoint tap"),
    ]

    wires = [
        # Network A.
        [1, RED_CONNECTOR, 5, RED_CONNECTOR],
        [5, RED_CONNECTOR, 6, RED_CONNECTOR],
        [6, RED_CONNECTOR, 7, RED_CONNECTOR],
        [7, RED_CONNECTOR, 8, RED_CONNECTOR],
        [8, RED_CONNECTOR, 9, RED_CONNECTOR],
        [9, RED_CONNECTOR, 3, RED_CONNECTOR],
        # Network B.
        [2, RED_CONNECTOR, 10, RED_CONNECTOR],
        [10, RED_CONNECTOR, 11, RED_CONNECTOR],
        [11, RED_CONNECTOR, 12, RED_CONNECTOR],
        [12, RED_CONNECTOR, 13, RED_CONNECTOR],
        [13, RED_CONNECTOR, 14, RED_CONNECTOR],
        [14, RED_CONNECTOR, 4, RED_CONNECTOR],
    ]

    return {
        "item": "blueprint",
        "label": case.label,
        "version": FACTORIO_BLUEPRINT_VERSION,
        "icons": [{"signal": {"type": "virtual", "name": "signal-A"}, "index": 1}],
        "entities": entities,
        "wires": wires,
    }


def generate_dense_bus_probe_blueprint_string(case: ProbeCase) -> str:
    """Return one importable probe blueprint string."""

    return encode_blueprint(build_dense_bus_probe_blueprint(case))


def _write_all(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for case in PROBE_CASES:
        path = output_dir / f"dense-bus-{case.slug}.txt"
        path.write_text(generate_dense_bus_probe_blueprint_string(case) + "\n", encoding="utf-8")
        print(f"wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("probe-blueprints"),
        help="directory for the four blueprint strings (default: probe-blueprints)",
    )
    args = parser.parse_args()
    _write_all(args.output_dir)


if __name__ == "__main__":
    main()
