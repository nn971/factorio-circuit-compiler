from collections import Counter

from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.devices.player_movement_detector import (
    DIRECTION_ORDER,
    DIRECTION_SIGNALS,
    INDICATOR_POSITIONS,
    SENSOR_POSITIONS,
    build_player_movement_detector_blueprint,
    generate_player_movement_detector_blueprint_string,
)


def test_direction_lanes_are_compass_arrow_signals() -> None:
    assert DIRECTION_SIGNALS == {
        "N": "up-arrow",
        "NE": "up-right-arrow",
        "E": "right-arrow",
        "SE": "down-right-arrow",
        "S": "down-arrow",
        "SW": "down-left-arrow",
        "W": "left-arrow",
        "NW": "up-left-arrow",
    }


def test_player_movement_detector_preserves_tested_layout_and_fixed_lanes() -> None:
    blueprint = build_player_movement_detector_blueprint()
    entities = blueprint["entities"]
    counts = Counter(entity["name"] for entity in entities)

    assert counts == {
        "stone-wall": 24,
        "gate": 8,
        "solar-panel": 4,
        "small-lamp": 8,
    }

    by_position = {
        (entity["position"]["x"], entity["position"]["y"]): entity for entity in entities
    }
    for direction in DIRECTION_ORDER:
        lane = DIRECTION_SIGNALS[direction]

        sensor = by_position[SENSOR_POSITIONS[direction]]
        assert sensor["name"] == "stone-wall"
        assert sensor["control_behavior"] == {
            "circuit_open_gate": True,
            "circuit_read_sensor": True,
            "output_signal": {"type": "virtual", "name": lane},
        }

        indicator = by_position[INDICATOR_POSITIONS[direction]]
        assert indicator["name"] == "small-lamp"
        assert indicator["control_behavior"]["circuit_condition"]["first_signal"] == {
            "type": "virtual",
            "name": lane,
        }


def test_all_direction_sensors_and_indicators_share_one_green_bus() -> None:
    blueprint = build_player_movement_detector_blueprint()
    entities = blueprint["entities"]
    by_position = {
        (entity["position"]["x"], entity["position"]["y"]): entity for entity in entities
    }
    expected_ids = {
        by_position[position]["entity_number"]
        for position in (*SENSOR_POSITIONS.values(), *INDICATOR_POSITIONS.values())
    }

    adjacency: dict[int, set[int]] = {}
    for source, source_connector, target, target_connector in blueprint["wires"]:
        assert source_connector == 2
        assert target_connector == 2
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)

    start = next(iter(expected_ids))
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for neighbor in adjacency.get(current, ()):
            if neighbor not in seen:
                seen.add(neighbor)
                frontier.append(neighbor)

    assert expected_ids <= seen


def test_blueprint_string_round_trips() -> None:
    encoded = generate_player_movement_detector_blueprint_string()
    assert decode_blueprint(encoded) == build_player_movement_detector_blueprint()
