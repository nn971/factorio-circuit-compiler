"""Stage a stable RoutedWire hash matching the old PYTHONHASHSEED=0 trajectory."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ROUTING = ROOT / "src/factorio_circuit/blueprint/routing.py"
ANNEALER = ROOT / "src/factorio_circuit/synthesis/incremental_joint_layout.py"


def _replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one staging anchor, found {count}")
    return text.replace(old, new)


def _seed_zero_color_hashes() -> tuple[int, int]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    raw = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "import json; print(json.dumps([hash('red'), hash('green')]))",
        ],
        env=env,
        text=True,
    )
    red, green = json.loads(raw)
    return int(red), int(green)


def _stage_routed_wire_hash(red_hash: int, green_hash: int) -> None:
    text = ROUTING.read_text()
    if "_PYTHONHASHSEED0_RED_HASH" in text:
        return
    old = """@dataclass(frozen=True, slots=True)\nclass RoutedWire:\n    source_entity: int\n    source_connector_id: int\n    target_entity: int\n    target_connector_id: int\n    color: WireColor\n\n    def as_factorio_tuple(self) -> tuple[int, int, int, int]:\n"""
    new = f"""class _FixedHashToken:\n    __slots__ = (\"value\",)\n\n    def __init__(self, value: int) -> None:\n        self.value = value\n\n    def __hash__(self) -> int:\n        return self.value\n\n\n# Preserve the RoutedWire hash produced by CPython 3.12 with PYTHONHASHSEED=0.\n# The pre-Milestone-C annealer iterated sets of RoutedWire values; keeping that exact hash\n# makes the old trajectory deterministic instead of replacing it with a new sorted order.\n_PYTHONHASHSEED0_RED_HASH = {red_hash}\n_PYTHONHASHSEED0_GREEN_HASH = {green_hash}\n_RED_HASH_TOKEN = _FixedHashToken(_PYTHONHASHSEED0_RED_HASH)\n_GREEN_HASH_TOKEN = _FixedHashToken(_PYTHONHASHSEED0_GREEN_HASH)\n\n\n@dataclass(frozen=True, slots=True)\nclass RoutedWire:\n    source_entity: int\n    source_connector_id: int\n    target_entity: int\n    target_connector_id: int\n    color: WireColor\n\n    def __hash__(self) -> int:\n        color_token = _RED_HASH_TOKEN if self.color is WireColor.RED else _GREEN_HASH_TOKEN\n        return hash(\n            (\n                self.source_entity,\n                self.source_connector_id,\n                self.target_entity,\n                self.target_connector_id,\n                color_token,\n            )\n        )\n\n    def as_factorio_tuple(self) -> tuple[int, int, int, int]:\n"""
    ROUTING.write_text(_replace_once(text, old, new))


def _stage_pre_c_proposal_order() -> None:
    text = ANNEALER.read_text()
    old = "for wire in sorted(affected, key=_routed_wire_sort_key):\n"
    if "for wire in affected:\n" in text[text.index("def proposal_delta(") : text.index("@dataclass(slots=True)\nclass _ExactObjectiveTracker")]:
        return
    start = text.index("def proposal_delta(")
    end = text.index("@dataclass(slots=True)\nclass _ExactObjectiveTracker")
    section = text[start:end]
    section = _replace_once(section, old, "for wire in affected:\n")
    ANNEALER.write_text(text[:start] + section + text[end:])


def main() -> None:
    red_hash, green_hash = _seed_zero_color_hashes()
    print(f"PYTHONHASHSEED=0 color hashes: red={red_hash} green={green_hash}")
    _stage_routed_wire_hash(red_hash, green_hash)
    _stage_pre_c_proposal_order()


if __name__ == "__main__":
    main()
