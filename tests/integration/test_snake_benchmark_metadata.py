from __future__ import annotations

import json
from pathlib import Path


BASELINES = Path(__file__).parents[2] / "benchmarks" / "snake" / "baselines.json"


def test_snake_benchmark_baselines_are_appendable_structured_records() -> None:
    payload = json.loads(BASELINES.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["benchmark"] == "snake-16x16"
    milestones = payload["milestones"]
    assert len(milestones) >= 2
    ids = [milestone["id"] for milestone in milestones]
    assert len(ids) == len(set(ids))

    for milestone in milestones:
        assert milestone["commit"]
        assert isinstance(milestone["in_game_validated"], bool)
        metrics = milestone["metrics"]
        assert metrics["implementation_combinators"] > 0
        assert metrics["physical_entities"] > 0
        assert metrics["layout_relays"] > 0
        width, height = metrics["extent_tiles"]
        assert width > 0
        assert height > 0

    assert milestones[-1]["in_game_validated"] is True
