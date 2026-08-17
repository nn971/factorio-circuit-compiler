from factorio_circuit.devices._blueprint import decode_blueprint
from factorio_circuit.probes.dense_bus_geometry import (
    PROBE_CASES,
    build_dense_bus_probe_blueprint,
    generate_dense_bus_probe_blueprint_string,
)


def test_dense_bus_probe_cases_isolate_offset_and_spacing() -> None:
    assert [
        (case.slug, case.first_bus_offset, case.track_spacing)
        for case in PROBE_CASES
    ] == [
        ("control", 3.0, 2.0),
        ("half-offset", 3.5, 2.0),
        ("unit-spacing", 3.0, 1.0),
        ("dense", 3.5, 1.0),
    ]


def test_dense_bus_probe_has_two_small_independent_networks() -> None:
    for case in PROBE_CASES:
        blueprint = build_dense_bus_probe_blueprint(case)
        entities = blueprint["entities"]
        wires = blueprint["wires"]

        assert len(entities) == 14
        assert len(wires) == 12
        assert blueprint["label"] == case.label

        positions = {
            entity["entity_number"]: (
                entity["position"]["x"],
                entity["position"]["y"],
            )
            for entity in entities
        }
        assert positions[5][1] == -case.first_bus_offset
        assert positions[10][1] == -(case.first_bus_offset + case.track_spacing)

        # The two logical networks use disjoint entity-number sets.
        network_a = {1, 3, 5, 6, 7, 8, 9}
        network_b = {2, 4, 10, 11, 12, 13, 14}
        for left, _left_connector, right, _right_connector in wires:
            assert (left in network_a and right in network_a) or (
                left in network_b and right in network_b
            )


def test_dense_bus_probe_blueprints_round_trip_through_codec() -> None:
    for case in PROBE_CASES:
        encoded = generate_dense_bus_probe_blueprint_string(case)
        assert encoded.startswith("0")
        assert decode_blueprint(encoded) == build_dense_bus_probe_blueprint(case)
