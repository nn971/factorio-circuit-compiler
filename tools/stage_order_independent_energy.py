"""Temporarily stage order-independent local wire-energy accumulation for Milestone C."""

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
    if "from math import ceil, exp, floor, fsum, hypot\n" in text:
        return
    text = _replace_once(
        text,
        "from math import ceil, exp, floor, hypot\n",
        "from math import ceil, exp, floor, fsum, hypot\n",
    )
    text = _replace_once(
        text,
        """        delta = 0.0\n        for wire in sorted(affected, key=_routed_wire_sort_key):\n            source_before = state.object_position(wire.source_entity)\n            target_before = state.object_position(wire.target_entity)\n            source_after = targets.get(wire.source_entity, source_before)\n            target_after = targets.get(wire.target_entity, target_before)\n            after_distance = _distance(source_after, target_after)\n            if after_distance > state.safe_span + _EPSILON:\n                return None\n            delta += _wire_energy(after_distance, state.safe_span)\n            delta -= _wire_energy(_distance(source_before, target_before), state.safe_span)\n        return delta\n""",
        """        contributions: list[float] = []\n        for wire in affected:\n            source_before = state.object_position(wire.source_entity)\n            target_before = state.object_position(wire.target_entity)\n            source_after = targets.get(wire.source_entity, source_before)\n            target_after = targets.get(wire.target_entity, target_before)\n            after_distance = _distance(source_after, target_after)\n            if after_distance > state.safe_span + _EPSILON:\n                return None\n            contributions.append(\n                _wire_energy(after_distance, state.safe_span)\n                - _wire_energy(_distance(source_before, target_before), state.safe_span)\n            )\n        return fsum(contributions)\n""",
    )
    text = _replace_once(
        text,
        """        delta = 0.0\n        for wire in sorted(affected, key=_routed_wire_sort_key):\n            source_before = state.object_position(wire.source_entity)\n            target_before = state.object_position(wire.target_entity)\n            source_after = targets.get(wire.source_entity, source_before)\n            target_after = targets.get(wire.target_entity, target_before)\n            delta += _distance(source_after, target_after)\n            delta -= _distance(source_before, target_before)\n        return delta\n""",
        """        return fsum(\n            _distance(\n                targets.get(wire.source_entity, state.object_position(wire.source_entity)),\n                targets.get(wire.target_entity, state.object_position(wire.target_entity)),\n            )\n            - _distance(\n                state.object_position(wire.source_entity),\n                state.object_position(wire.target_entity),\n            )\n            for wire in affected\n        )\n""",
    )
    PATH.write_text(text)


if __name__ == "__main__":
    main()
