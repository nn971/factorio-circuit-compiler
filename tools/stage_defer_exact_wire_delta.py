"""Stage the Milestone C exact-tracker bookkeeping deferral experiment."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANNEALER = ROOT / "src/factorio_circuit/synthesis/incremental_joint_layout.py"
TEST = ROOT / "tests/synthesis/test_exact_tracker_hot_loop.py"


def _replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one staging anchor, found {count}")
    return text.replace(old, new)


def main() -> None:
    text = ANNEALER.read_text()
    old = '''            exact_wire_delta = (\n                exact_tracker.proposal_wire_length_delta(state, topology, targets)\n                if exact_tracker is not None\n                else 0.0\n            )\n\n            compact_delta = sum(\n'''
    new = '''            compact_delta = sum(\n'''
    if old in text:
        text = _replace_once(text, old, new)

    old_accept = '''            if delta > 0 and rng.random() >= exp(-delta / temperature):\n                continue\n\n            occupancy.remove(object_id, current)\n'''
    new_accept = '''            if delta > 0 and rng.random() >= exp(-delta / temperature):\n                continue\n\n            exact_wire_delta = (\n                exact_tracker.proposal_wire_length_delta(state, topology, targets)\n                if exact_tracker is not None\n                else 0.0\n            )\n            occupancy.remove(object_id, current)\n'''
    if old_accept in text:
        text = _replace_once(text, old_accept, new_accept)
    ANNEALER.write_text(text)

    TEST.write_text(
        '''from __future__ import annotations\n\nfrom benchmarks.layout_optimizer_topology_corpus import _clustered_sparse_cut_case\nfrom factorio_circuit.synthesis import incremental_joint_layout as incremental\nfrom factorio_circuit.synthesis.layout_optimizer import optimize_physical_layout\nfrom factorio_circuit.synthesis.placement import PlacementOptions\n\n\ndef test_exact_wire_delta_is_computed_only_for_accepted_moves(monkeypatch) -> None:\n    wire_delta_calls = 0\n    accepted_score_calls = 0\n    original_wire_delta = incremental._ExactObjectiveTracker.proposal_wire_length_delta\n    original_score = incremental._accepted_move_exact_score\n\n    def counted_wire_delta(self, state, topology, targets):\n        nonlocal wire_delta_calls\n        wire_delta_calls += 1\n        return original_wire_delta(self, state, topology, targets)\n\n    def counted_score(state, topology, center, tracker):\n        nonlocal accepted_score_calls\n        accepted_score_calls += 1\n        return original_score(state, topology, center, tracker)\n\n    monkeypatch.setattr(\n        incremental._ExactObjectiveTracker,\n        "proposal_wire_length_delta",\n        counted_wire_delta,\n    )\n    monkeypatch.setattr(incremental, "_accepted_move_exact_score", counted_score)\n\n    case = _clustered_sparse_cut_case()\n    optimize_physical_layout(\n        case.problem,\n        options=PlacementOptions(\n            anchor_io=False,\n            reserve_corridors=False,\n            iterations=512,\n            random_seed=3,\n            restarts=1,\n        ),\n    )\n\n    assert accepted_score_calls > 0\n    assert wire_delta_calls == accepted_score_calls\n'''
    )


if __name__ == "__main__":
    main()
