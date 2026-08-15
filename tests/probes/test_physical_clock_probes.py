"""Structural smoke tests for the raw Factorio mechanics probes."""

from probes.belt_event_timing import build_blueprint as build_belt_probe
from probes.blueprint_utils import decode_blueprint, encode_blueprint
from probes.sum_into_boundary import build_blueprint as build_sum_probe
from probes.vector_conditional_forward import build_blueprint as build_vector_probe


def _roundtrip(payload: dict[str, object]) -> dict[str, object]:
    encoded = encode_blueprint(payload)
    assert encoded.startswith("0")
    return decode_blueprint(encoded)


def test_sum_into_boundary_probe_roundtrips() -> None:
    payload = build_sum_probe()
    assert _roundtrip(payload) == payload
    blueprint = payload["blueprint"]
    assert isinstance(blueprint, dict)
    assert len(blueprint["entities"]) == 12


def test_vector_forward_probe_roundtrips() -> None:
    payload = build_vector_probe()
    assert _roundtrip(payload) == payload
    blueprint = payload["blueprint"]
    assert isinstance(blueprint, dict)
    decider = blueprint["entities"][2]
    output = decider["control_behavior"]["decider_conditions"]["outputs"][0]
    assert output["signal"]["name"] == "signal-everything"
    assert output["networks"] == {"red": True, "green": False}


def test_belt_event_probe_uses_pulse_mode_and_one_tick_delay() -> None:
    payload = build_belt_probe()
    assert _roundtrip(payload) == payload
    blueprint = payload["blueprint"]
    assert isinstance(blueprint, dict)
    read_belt = blueprint["entities"][2]
    assert read_belt["control_behavior"]["circuit_read_hand_contents"] is True
    assert read_belt["control_behavior"]["circuit_contents_read_mode"] == 0
