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
        assert metrics["state_period_ticks"] > 0

        # Layout snapshots are optional: some accepted compiler milestones deliberately record only
        # the durable pre-synthesis census when a fresh relay/extent measurement was not taken.
        if "physical_entities" in metrics:
            assert metrics["physical_entities"] > 0
        if "abstract_physical_entities" in metrics:
            assert metrics["abstract_physical_entities"] > 0
        if "layout_relays" in metrics:
            assert metrics["layout_relays"] > 0
        if "extent_tiles" in metrics:
            width, height = metrics["extent_tiles"]
            assert width > 0
            assert height > 0

    assert milestones[-1]["in_game_validated"] is True


def test_accepted_snake_layout_history_is_not_rewritten() -> None:
    payload = json.loads(BASELINES.read_text(encoding="utf-8"))
    by_id = {milestone["id"]: milestone for milestone in payload["milestones"]}

    initial = by_id["interactive-snake-initial"]
    assert initial["commit"] == "5e10020763ee2b9b5036eb82f0d82d86e3d2c673"
    assert initial["in_game_validated"] is True
    assert initial["metrics"]["layout_relays"] == 470_732
    assert initial["metrics"]["extent_tiles"] == [3004, 2792]

    dense = by_id["dense-safe-folded-v1"]
    assert dense["commit"] == "1bde1650a393d881bd04e275197ec39ed2245e10"
    assert dense["in_game_validated"] is True
    assert dense["metrics"]["implementation_combinators"] == 5_657
    assert dense["metrics"]["layout_relays"] == 246_476
    assert dense["metrics"]["extent_tiles"] == [1554, 1544]
    assert dense["metrics"]["state_period_ticks"] == 60

    settling_alap = by_id["settling-alap-v1"]
    assert settling_alap["in_game_validated"] is True
    assert settling_alap["metrics"]["implementation_combinators"] == 1_131
    assert settling_alap["metrics"]["abstract_physical_entities"] == 1_142
    assert settling_alap["metrics"]["phase_delay_combinators"] == 430
    assert settling_alap["metrics"]["state_period_ticks"] == 60
    assert "layout_relays" not in settling_alap["metrics"]
    assert "extent_tiles" not in settling_alap["metrics"]
