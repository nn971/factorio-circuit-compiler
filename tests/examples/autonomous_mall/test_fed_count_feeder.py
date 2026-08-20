import pytest

from examples.autonomous_mall.complete_controller import build_complete_blueprint_book


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
    raw = blueprint["entities"]
    assert isinstance(raw, list)
    assert all(isinstance(entity, dict) for entity in raw)
    return raw  # type: ignore[return-value]


def _described(entities: list[dict[str, object]], marker: str) -> list[dict[str, object]]:
    return [entity for entity in entities if marker in str(entity.get("player_description", ""))]


def _wire_set(blueprint: dict[str, object]) -> set[tuple[int, int, int, int]]:
    raw = blueprint["wires"]
    assert isinstance(raw, list)
    return {tuple(int(value) for value in wire) for wire in raw}  # type: ignore[misc]


def _normalized_wire(
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> tuple[int, int, int, int]:
    if left > right:
        return right, right_connector, left, left_connector
    return left, left_connector, right, right_connector


@pytest.mark.slow
@pytest.mark.acceptance
@pytest.mark.parametrize("entry_index", [1, 2, 3])
def test_complete_worker_counts_feeder_hand_pulses(entry_index: int, complete_book) -> None:
    blueprint = _entry(complete_book, entry_index)
    entities = _entities(blueprint)

    feeder_matches = _described(entities, "MALL DEVICE feeder")
    memory_matches = _described(entities, "fed-count accumulator")
    negate_matches = _described(entities, "negate fed-count")
    old_negate_matches = _described(entities, "negate machine contents")

    assert len(feeder_matches) == 1
    assert len(memory_matches) == 1
    assert len(negate_matches) == 1
    assert old_negate_matches == []

    feeder = feeder_matches[0]
    memory = memory_matches[0]
    negate = negate_matches[0]

    behavior = feeder["control_behavior"]
    assert isinstance(behavior, dict)
    assert behavior["input_networks"] == {"red": False, "green": True}
    assert behavior["output_networks"] == {"red": True, "green": False}
    assert behavior["circuit_read_hand_contents"] is True
    assert behavior["circuit_hand_read_mode"] == 0
    assert feeder["override_stack_size"] == 1

    memory_behavior = memory["control_behavior"]
    assert isinstance(memory_behavior, dict)
    params = memory_behavior["decider_conditions"]
    assert isinstance(params, dict)
    conditions = params["conditions"]
    outputs = params["outputs"]
    assert isinstance(conditions, list) and len(conditions) == 1
    assert isinstance(outputs, list) and len(outputs) == 1
    assert conditions[0]["first_signal"] == {"type": "virtual", "name": "signal-E"}
    assert conditions[0]["first_signal_networks"] == {"red": False, "green": True}
    assert conditions[0]["comparator"] == ">"
    assert outputs[0]["signal"] == {"type": "virtual", "name": "signal-each"}
    assert outputs[0]["copy_count_from_input"] is True
    assert outputs[0]["networks"] == {"red": True, "green": False}

    feeder_id = int(feeder["entity_number"])
    memory_id = int(memory["entity_number"])
    negate_id = int(negate["entity_number"])
    wires = _wire_set(blueprint)

    # The memory cell is a gated self-loop and feeds the repurposed -fed arithmetic stage.
    assert _normalized_wire(memory_id, 3, memory_id, 1) in wires
    assert _normalized_wire(memory_id, 3, negate_id, 1) in wires

    # Feeder connector 1 is the RED hand-pulse output; connector 2 is the GREEN control input.
    assert any(
        (wire[0] == feeder_id and wire[1] == 1) or (wire[2] == feeder_id and wire[3] == 1)
        for wire in wires
    )
    assert any(
        (wire[0] == feeder_id and wire[1] == 2) or (wire[2] == feeder_id and wire[3] == 2)
        for wire in wires
    )


@pytest.mark.slow
@pytest.mark.acceptance
def test_complete_row_has_one_fed_counter_per_worker(complete_book) -> None:
    blueprint = _entry(complete_book, 0)
    entities = _entities(blueprint)

    assert len(_described(entities, "fed-count accumulator")) == 5
    assert len(_described(entities, "negate fed-count")) == 5
    assert _described(entities, "negate machine contents") == []
