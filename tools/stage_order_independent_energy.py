"""Temporarily stage stable incident-order wire accumulation for Milestone C."""

from __future__ import annotations

from pathlib import Path

PATH = (
    Path(__file__).resolve().parents[1]
    / "src/factorio_circuit/synthesis/incremental_joint_layout.py"
)


def _replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one staging anchor, found {count}")
    return text.replace(old, new)


def main() -> None:
    text = PATH.read_text()
    if "affected_seen: set[wire_routing.RoutedWire]" in text:
        return
    old_collect = """        affected: set[wire_routing.RoutedWire] = set()\n        for object_id in targets:\n            affected.update(self.incident_wires.get(object_id, ()))\n\n        delta = 0.0\n        for wire in sorted(affected, key=_routed_wire_sort_key):\n"""
    new_collect = """        affected: list[wire_routing.RoutedWire] = []\n        affected_seen: set[wire_routing.RoutedWire] = set()\n        for object_id in targets:\n            for wire in self.incident_wires.get(object_id, ()):\n                if wire in affected_seen:\n                    continue\n                affected_seen.add(wire)\n                affected.append(wire)\n\n        delta = 0.0\n        for wire in affected:\n"""
    text = _replace_once(text, old_collect, new_collect)

    old_tracker_collect = """        affected: set[wire_routing.RoutedWire] = set()\n        for object_id in targets:\n            affected.update(topology.incident_wires.get(object_id, ()))\n        delta = 0.0\n        for wire in sorted(affected, key=_routed_wire_sort_key):\n"""
    new_tracker_collect = """        affected: list[wire_routing.RoutedWire] = []\n        affected_seen: set[wire_routing.RoutedWire] = set()\n        for object_id in targets:\n            for wire in topology.incident_wires.get(object_id, ()):\n                if wire in affected_seen:\n                    continue\n                affected_seen.add(wire)\n                affected.append(wire)\n        delta = 0.0\n        for wire in affected:\n"""
    text = _replace_once(text, old_tracker_collect, new_tracker_collect)
    PATH.write_text(text)


if __name__ == "__main__":
    main()
