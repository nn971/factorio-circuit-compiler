from __future__ import annotations

from math import sqrt

from factorio_circuit.devices._blueprint import decode_blueprint, encode_blueprint
from factorio_circuit.probes.integer_dense_fold_geometry import (
    PROBES,
    build_integer_red_fold_probe,
    build_integer_two_color_fold_probe,
)


def _entity_positions(blueprint: dict[str, object]) -> dict[int, tuple[float, float]]:
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    positions: dict[int, tuple[float, float]] = {}
    for raw_entity in entities:
        assert isinstance(raw_entity, dict)
        entity_number = raw_entity["entity_number"]
        position = raw_entity["position"]
        assert isinstance(entity_number, int)
        assert isinstance(position, dict)
        x = position["x"]
        y = position["y"]
        assert isinstance(x, (int, float))
        assert isinstance(y, (int, float))
        positions[entity_number] = (float(x), float(y))
    return positions


def test_integer_dense_fold_probes_keep_every_entity_on_one_coordinate_phase() -> None:
    for blueprint in (build_integer_red_fold_probe(), build_integer_two_color_fold_probe()):
        for x, y in _entity_positions(blueprint).values():
            assert abs(x - round(x)) < 1e-9
            assert abs(y - round(y)) < 1e-9


def test_integer_dense_fold_probe_wires_fit_safe_span_and_have_real_endpoints() -> None:
    safe_span = 7.0
    for blueprint in (build_integer_red_fold_probe(), build_integer_two_color_fold_probe()):
        positions = _entity_positions(blueprint)
        wires = blueprint["wires"]
        assert isinstance(wires, list)
        for raw_wire in wires:
            assert isinstance(raw_wire, list)
            left, _left_connector, right, _right_connector = raw_wire
            assert isinstance(left, int)
            assert isinstance(right, int)
            assert left != right
            left_position = positions[left]
            right_position = positions[right]
            dx = left_position[0] - right_position[0]
            dy = left_position[1] - right_position[1]
            assert sqrt(dx * dx + dy * dy) <= safe_span + 1e-9


def test_integer_dense_fold_probe_blueprints_round_trip_through_codec() -> None:
    expected = {
        "integer-dense-fold-red": build_integer_red_fold_probe(),
        "integer-dense-fold-red-green": build_integer_two_color_fold_probe(),
    }
    assert dict(PROBES) == expected
    for _slug, blueprint in PROBES:
        encoded = encode_blueprint(blueprint)
        assert encoded.startswith("0")
        assert decode_blueprint(encoded) == blueprint
