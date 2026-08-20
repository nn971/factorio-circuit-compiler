from collections import Counter
from math import hypot

import pytest

from examples.autonomous_mall.complete_controller import build_complete_blueprint_book
from examples.autonomous_mall.device_tiles import COMPLETE_TILE_HEIGHT, TILE_WIDTH


@pytest.fixture(scope="module")
def complete_book() -> dict[str, object]:
    return build_complete_blueprint_book()


def _entry(book: dict[str, object], index: int) -> dict[str, object]:
    root = book["blueprint_book"]
    assert isinstance(root, dict)
    entries = root["blueprints"]
    assert isinstance(entries, list)
    entry = entries[index]
    assert isinstance(entry, dict)
    blueprint = entry["blueprint"]
    assert isinstance(blueprint, dict)
    return blueprint


def _entities(blueprint: dict[str, object]) -> list[dict[str, object]]:
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    assert all(isinstance(entity, dict) for entity in entities)
    return entities  # type: ignore[return-value]


def _one(entities: list[dict[str, object]], name: str) -> dict[str, object]:
    matches = [entity for entity in entities if entity.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _described(entities: list[dict[str, object]], text: str) -> list[dict[str, object]]:
    return [entity for entity in entities if text in str(entity.get("player_description", ""))]


def _requested_module(machine: dict[str, object]) -> str:
    plans = machine["items"]
    assert isinstance(plans, list) and len(plans) == 1
    plan = plans[0]
    assert isinstance(plan, dict)
    item_id = plan["id"]
    assert isinstance(item_id, dict)
    positions = plan["items"]
    assert isinstance(positions, dict)
    inventory = positions["in_inventory"]
    assert isinstance(inventory, list)
    assert [slot["stack"] for slot in inventory] == [0, 1, 2, 3]
    assert all(slot["inventory"] == 4 and slot["count"] == 1 for slot in inventory)
    return str(item_id["name"])


def _assert_wire_reach(blueprint: dict[str, object], maximum: float = 9.0) -> None:
    entities = _entities(blueprint)
    positions = {
        int(entity["entity_number"]): (
            float(entity["position"]["x"]),
            float(entity["position"]["y"]),
        )
        for entity in entities
    }
    wires = blueprint["wires"]
    assert isinstance(wires, list)
    for left, _left_connector, right, _right_connector in wires:
        left_position = positions[int(left)]
        right_position = positions[int(right)]
        distance = hypot(
            left_position[0] - right_position[0],
            left_position[1] - right_position[1],
        )
        assert distance <= maximum + 1e-9


@pytest.mark.slow
@pytest.mark.acceptance
def test_complete_worker_roles_and_machine_controls(complete_book) -> None:
    productivity = _entry(complete_book, 1)
    quality = _entry(complete_book, 2)
    recycler = _entry(complete_book, 3)

    for blueprint in (productivity, quality, recycler):
        assert blueprint["snap-to-grid"] == {
            "x": TILE_WIDTH,
            "y": COMPLETE_TILE_HEIGHT,
        }
        assert blueprint["absolute-snapping"] is True
        names = Counter(entity["name"] for entity in _entities(blueprint))
        assert names["requester-chest"] == 1
        assert names["passive-provider-chest"] == 1
        assert names["logistic-chest-requester"] == 0
        assert names["logistic-chest-passive-provider"] == 0
        _assert_wire_reach(blueprint)

    assert Counter(entity["name"] for entity in _entities(productivity))["stack-inserter"] == 2
    assert Counter(entity["name"] for entity in _entities(quality))["stack-inserter"] == 2
    assert Counter(entity["name"] for entity in _entities(recycler))["stack-inserter"] == 1

    p_machine = _one(_entities(productivity), "assembling-machine-3")
    q_machine = _one(_entities(quality), "assembling-machine-3")
    r_machine = _one(_entities(recycler), "recycler")

    assert "direction" not in p_machine
    assert "direction" not in q_machine
    assert r_machine["direction"] == 0

    assert _requested_module(p_machine) == "productivity-module-3"
    assert _requested_module(q_machine) == "quality-module-3"
    assert _requested_module(r_machine) == "quality-module-3"

    for machine in (p_machine, q_machine):
        behavior = machine["control_behavior"]
        assert isinstance(behavior, dict)
        assert behavior["set_recipe"] is True
        assert behavior["read_contents"] is True
        assert behavior["read_working"] is True
        assert behavior["read_recipe_finished"] is True
        assert behavior["input_networks"] == {"red": False, "green": True}
        assert behavior["output_networks"] == {"red": True, "green": False}

    recycler_behavior = r_machine["control_behavior"]
    assert isinstance(recycler_behavior, dict)
    assert "set_recipe" not in recycler_behavior
    assert recycler_behavior["read_contents"] is True
    assert recycler_behavior["read_working"] is True
    assert recycler_behavior["read_recipe_finished"] is True


@pytest.mark.slow
@pytest.mark.acceptance
def test_assembler_and_recycler_use_different_item_flow_geometry(complete_book) -> None:
    productivity = _entry(complete_book, 1)
    recycler = _entry(complete_book, 3)

    p_entities = _entities(productivity)
    p_machine = _one(p_entities, "assembling-machine-3")
    p_feeder = _described(p_entities, "MALL DEVICE feeder")[0]
    p_output = _described(p_entities, "MALL DEVICE output inserter")[0]
    assert p_feeder["direction"] == 2
    assert p_output["direction"] == 2

    r_entities = _entities(recycler)
    r_machine = _one(r_entities, "recycler")
    r_feeder = _described(r_entities, "MALL DEVICE feeder")[0]
    r_requester = _one(r_entities, "requester-chest")
    r_provider = _one(r_entities, "passive-provider-chest")
    assert not _described(r_entities, "MALL DEVICE output inserter")

    mx = float(r_machine["position"]["x"])
    my = float(r_machine["position"]["y"])
    assert (mx, my) == (17.0, 59.0)
    assert r_machine["direction"] == 0
    assert r_feeder["position"] == {"x": 16.5, "y": 61.5}
    assert r_feeder["direction"] == 0
    assert r_requester["position"] == {"x": 16.5, "y": 62.5}
    # Recycler prototype vector_to_place_result is (-0.35, -2.3); this is its north output tile.
    assert r_provider["position"] == {"x": 16.5, "y": 56.5}


@pytest.mark.slow
@pytest.mark.acceptance
def test_complete_worker_has_feeder_and_completion_latch(complete_book) -> None:
    productivity = _entry(complete_book, 1)
    entities = _entities(productivity)

    feeder = next(
        entity
        for entity in entities
        if str(entity.get("player_description", "")).startswith("MALL DEVICE feeder")
    )
    assert feeder["override_stack_size"] == 1
    behavior = feeder["control_behavior"]
    assert isinstance(behavior, dict)
    assert behavior["circuit_set_filters"] is True
    assert behavior["circuit_enabled"] is True
    condition = behavior["circuit_condition"]
    assert isinstance(condition, dict)
    assert condition["first_signal"] == {"type": "virtual", "name": "signal-E"}

    descriptions = {str(entity.get("player_description", "")) for entity in entities}
    assert "MALL DEVICE negate machine contents" in descriptions
    assert "MALL DEVICE positive missing ingredients -> inserter filters" in descriptions
    assert "MALL DEVICE input-enable AND not-working" in descriptions
    assert "MALL DEVICE completion SET" in descriptions
    assert "MALL DEVICE completion HOLD until acknowledgement" in descriptions

    ids = {int(entity["entity_number"]) for entity in entities}
    wires = productivity["wires"]
    assert isinstance(wires, list)
    assert all(int(wire[0]) in ids and int(wire[2]) in ids for wire in wires)


@pytest.mark.slow
@pytest.mark.acceptance
def test_complete_row_has_five_devices_and_shared_seam_docks(complete_book) -> None:
    assembled = _entry(complete_book, 0)
    entities = _entities(assembled)
    names = Counter(entity["name"] for entity in entities)

    assert names["assembling-machine-3"] == 4
    assert names["recycler"] == 1
    assert names["requester-chest"] == 5
    assert names["passive-provider-chest"] == 5
    assert names["stack-inserter"] == 9
    assert names["logistic-chest-requester"] == 0
    assert names["logistic-chest-passive-provider"] == 0

    recycler = _one(entities, "recycler")
    assert recycler["direction"] == 0
    assert len(_described(entities, "R0: MALL DEVICE output inserter")) == 0

    docks = [
        entity
        for entity in entities
        if str(entity.get("player_description", "")).startswith("DOCK ")
    ]
    for seam in range(1, 6):
        x = seam * TILE_WIDTH
        seam_docks = [
            entity
            for entity in docks
            if float(entity["position"]["x"]) == x
            and float(entity["position"]["y"]) in {10.0, 14.0}
        ]
        assert len(seam_docks) == 2
