"""Generate cheap in-game probes for the integer dense folded-bus geometry.

These blueprints bypass semantic compilation and physical synthesis. They reproduce the
``safe-folded-crossbar`` geometry after the in-game diagnosis of the failed half-tile bus phase:

    first bus offset = 3.0 tiles
    track spacing    = 1.0 tile
    relay hop pitch  = 6.0 tiles

Both probes preserve the production multi-row invariant: the computation-row right edge is
``x = 0 (mod 6)``, while packed portal columns skip that residue. Every routing relay is therefore on
one integer blueprint-coordinate phase.

Every routed net has one constant source emitting ``signal-A`` with a unique count and one labelled
blank sink. Select a sink in game; it should see exactly the count written in its label.

Generate both import strings with::

    uv run python -m factorio_circuit.probes.integer_dense_fold_geometry \
        --output-dir probe-blueprints
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from math import ceil, floor
from pathlib import Path
from typing import Final, Literal

from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint

FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113
SAFE_PITCH: Final = 6.0
FIRST_BUS_OFFSET: Final = 3.0
TRACK_SPACING: Final = 1.0
FEEDER_OFFSET: Final = -2.0
RED_CONNECTOR: Final = 1
GREEN_CONNECTOR: Final = 2

WireColor = Literal["red", "green"]


@dataclass(slots=True)
class _Builder:
    label: str
    entities: list[dict[str, object]] = field(default_factory=list)
    wires: list[list[int]] = field(default_factory=list)
    _next_id: int = 1
    _relay_positions: dict[tuple[float, float], tuple[int, int]] = field(default_factory=dict)

    def constant(
        self,
        x: float,
        y: float,
        description: str,
        *,
        signal_count: int | None = None,
    ) -> int:
        entity_id = self._next_id
        self._next_id += 1
        entity: dict[str, object] = {
            "entity_number": entity_id,
            "name": "constant-combinator",
            "position": {"x": x, "y": y},
            "player_description": description,
        }
        if signal_count is not None:
            entity["control_behavior"] = {
                "sections": {
                    "sections": [
                        {
                            "index": 1,
                            "filters": [
                                {
                                    "index": 1,
                                    "type": "virtual",
                                    "name": "signal-A",
                                    "quality": "normal",
                                    "comparator": "=",
                                    "count": signal_count,
                                }
                            ],
                        }
                    ]
                }
            }
        self.entities.append(entity)
        return entity_id

    def relay(self, x: float, y: float, *, net: int, description: str) -> int:
        if abs(x - round(x)) > 1e-9 or abs(y - round(y)) > 1e-9:
            raise AssertionError(f"integer dense probe relay is off lattice: {(x, y)}")
        position = (x, y)
        previous = self._relay_positions.get(position)
        if previous is not None:
            previous_id, previous_net = previous
            if previous_net != net:
                raise AssertionError(
                    f"probe assigned relay site {position} to nets {previous_net} and {net}"
                )
            return previous_id
        relay_id = self.constant(x, y, description)
        self._relay_positions[position] = (relay_id, net)
        return relay_id

    def wire(self, left: int, right: int, color: WireColor) -> None:
        if left == right:
            return
        connector = RED_CONNECTOR if color == "red" else GREEN_CONNECTOR
        self.wires.append([left, connector, right, connector])

    def blueprint(self) -> Blueprint:
        return {
            "item": "blueprint",
            "label": self.label,
            "version": FACTORIO_BLUEPRINT_VERSION,
            "icons": [{"signal": {"type": "virtual", "name": "signal-A"}, "index": 1}],
            "entities": self.entities,
            "wires": self.wires,
        }


def _bus_y(row_y: float, color: WireColor, track: int) -> float:
    offset = FIRST_BUS_OFFSET + track * TRACK_SPACING
    return row_y - offset if color == "red" else row_y + offset


def _portal_lane_offset(ordinal: int) -> float:
    offset = 9
    seen = 0
    while seen < ordinal:
        offset += 1
        if offset % int(SAFE_PITCH) != 0:
            seen += 1
    return float(offset)


def _vertical_endpoint(
    builder: _Builder,
    *,
    entity: int,
    entity_x: float,
    entity_y: float,
    bus_y: float,
    net: int,
    color: WireColor,
) -> int:
    feeder_x = entity_x + FEEDER_OFFSET
    sign = -1.0 if bus_y < entity_y else 1.0
    nodes: list[int] = []
    y = entity_y + sign * SAFE_PITCH
    while y > bus_y if sign < 0 else y < bus_y:
        nodes.append(
            builder.relay(
                feeder_x,
                y,
                net=net,
                description=f"net {net} {color} endpoint feeder",
            )
        )
        y += sign * SAFE_PITCH
    nodes.append(
        builder.relay(
            feeder_x,
            bus_y,
            net=net,
            description=f"net {net} {color} endpoint tap",
        )
    )
    builder.wire(entity, nodes[0], color)
    for left, right in zip(nodes, nodes[1:], strict=False):
        builder.wire(left, right, color)
    return nodes[-1]


def _horizontal_segment(
    builder: _Builder,
    *,
    attachments: list[tuple[float, int]],
    bus_y: float,
    net: int,
    color: WireColor,
) -> None:
    min_x = min(x for x, _entity in attachments)
    max_x = max(x for x, _entity in attachments)
    nodes = list(attachments)
    x = ceil(min_x / SAFE_PITCH) * SAFE_PITCH
    last = floor(max_x / SAFE_PITCH) * SAFE_PITCH
    while x <= last + 1e-9:
        nodes.append(
            (
                x,
                builder.relay(
                    x,
                    bus_y,
                    net=net,
                    description=f"net {net} {color} row bus",
                ),
            )
        )
        x += SAFE_PITCH
    nodes.sort(key=lambda item: (item[0], item[1]))
    for (_left_x, left), (_right_x, right) in zip(nodes, nodes[1:], strict=False):
        builder.wire(left, right, color)


def _vertical_stitch(
    builder: _Builder,
    *,
    portal_x: float,
    upper_bus_y: float,
    lower_bus_y: float,
    net: int,
    color: WireColor,
) -> tuple[int, int]:
    top = builder.relay(
        portal_x,
        upper_bus_y,
        net=net,
        description=f"net {net} {color} upper fold tap",
    )
    bottom = builder.relay(
        portal_x,
        lower_bus_y,
        net=net,
        description=f"net {net} {color} lower fold tap",
    )
    nodes: list[tuple[float, int]] = [(upper_bus_y, top)]
    y = ceil(upper_bus_y / SAFE_PITCH) * SAFE_PITCH
    if y <= upper_bus_y + 1e-9:
        y += SAFE_PITCH
    while y < lower_bus_y - 1e-9:
        nodes.append(
            (
                y,
                builder.relay(
                    portal_x,
                    y,
                    net=net,
                    description=f"net {net} {color} fold stitch",
                ),
            )
        )
        y += SAFE_PITCH
    nodes.append((lower_bus_y, bottom))
    nodes.sort(key=lambda item: (item[0], item[1]))
    for (_left_y, left), (_right_y, right) in zip(nodes, nodes[1:], strict=False):
        builder.wire(left, right, color)
    return top, bottom


def _add_folded_net(
    builder: _Builder,
    *,
    net: int,
    expected: int,
    source_x: float,
    sink_x: float,
    row_pitch: float,
    right_edge: float,
    portal_ordinal: int,
    upper_track: int,
    lower_track: int,
    color: WireColor,
    label: str,
) -> None:
    upper_bus_y = _bus_y(0.0, color, upper_track)
    lower_bus_y = _bus_y(row_pitch, color, lower_track)
    portal_x = right_edge + _portal_lane_offset(portal_ordinal)
    if abs(portal_x % SAFE_PITCH) < 1e-9:
        raise AssertionError("production-style portal landed on row-bus x lattice")

    source = builder.constant(
        source_x,
        0.0,
        f"{label} SOURCE — signal-A={expected}",
        signal_count=expected,
    )
    sink = builder.constant(
        sink_x,
        row_pitch,
        f"{label} SINK — EXPECT signal-A={expected}",
    )
    source_tap = _vertical_endpoint(
        builder,
        entity=source,
        entity_x=source_x,
        entity_y=0.0,
        bus_y=upper_bus_y,
        net=net,
        color=color,
    )
    sink_tap = _vertical_endpoint(
        builder,
        entity=sink,
        entity_x=sink_x,
        entity_y=row_pitch,
        bus_y=lower_bus_y,
        net=net,
        color=color,
    )
    top_portal, bottom_portal = _vertical_stitch(
        builder,
        portal_x=portal_x,
        upper_bus_y=upper_bus_y,
        lower_bus_y=lower_bus_y,
        net=net,
        color=color,
    )
    _horizontal_segment(
        builder,
        attachments=[(source_x + FEEDER_OFFSET, source_tap), (portal_x, top_portal)],
        bus_y=upper_bus_y,
        net=net,
        color=color,
    )
    _horizontal_segment(
        builder,
        attachments=[(sink_x + FEEDER_OFFSET, sink_tap), (portal_x, bottom_portal)],
        bus_y=lower_bus_y,
        net=net,
        color=color,
    )


def build_integer_red_fold_probe(track_count: int = 12) -> Blueprint:
    """Exercise integer one-tile RED tracks with fold taps on and off the six-tile y lattice."""

    builder = _Builder("Integer dense fold probe — 12 RED nets")
    row_pitch = 30.0
    right_edge = 36.0  # thirteen 3-tile columns: production odd-column invariant
    builder.constant(right_edge, 0.0, "UNWIRED ROW-EDGE ANCHOR")
    builder.constant(right_edge, row_pitch, "UNWIRED ROW-EDGE ANCHOR")

    for index in range(track_count):
        _add_folded_net(
            builder,
            net=index + 1,
            expected=201 + index,
            source_x=index * 3.0,
            sink_x=(track_count - 1 - index) * 3.0,
            row_pitch=row_pitch,
            right_edge=right_edge,
            portal_ordinal=index,
            upper_track=index,
            lower_track=(index * 5) % track_count,
            color="red",
            label=f"RED FOLD {index}",
        )
    return builder.blueprint()


def build_integer_two_color_fold_probe(track_count: int = 8) -> Blueprint:
    """Exercise integer RED/GREEN bands and their vertical fold stitches together."""

    builder = _Builder("Integer dense fold probe — RED + GREEN")
    row_pitch = 36.0
    routed_slots = track_count * 2
    right_edge = 48.0  # seventeen 3-tile columns including the unconnected edge anchor
    builder.constant(right_edge, 0.0, "UNWIRED ROW-EDGE ANCHOR")
    builder.constant(right_edge, row_pitch, "UNWIRED ROW-EDGE ANCHOR")

    net = 0
    for color in ("red", "green"):
        for index in range(track_count):
            slot = net
            net += 1
            _add_folded_net(
                builder,
                net=net,
                expected=301 + slot,
                source_x=slot * 3.0,
                sink_x=(routed_slots - 1 - slot) * 3.0,
                row_pitch=row_pitch,
                right_edge=right_edge,
                portal_ordinal=slot,
                upper_track=index,
                lower_track=(index * 3) % track_count,
                color=color,
                label=f"{color.upper()} FOLD {index}",
            )
    return builder.blueprint()


PROBES: Final[tuple[tuple[str, Blueprint], ...]] = (
    ("integer-dense-fold-red", build_integer_red_fold_probe()),
    ("integer-dense-fold-red-green", build_integer_two_color_fold_probe()),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("probe-blueprints"),
        help="directory for the two blueprint strings (default: probe-blueprints)",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for slug, blueprint in PROBES:
        path = args.output_dir / f"{slug}.txt"
        path.write_text(encode_blueprint(blueprint) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
