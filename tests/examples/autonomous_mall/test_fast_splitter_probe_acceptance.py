import pytest

from examples.autonomous_mall.fast_splitter_probe import build_fast_splitter_probe_component
from factorio_circuit.devices._blueprint import encode_blueprint


@pytest.mark.acceptance
def test_fast_splitter_probe_builds_one_worker_with_control_seam() -> None:
    component = build_fast_splitter_probe_component(1)
    blueprint = component.anchored.blueprint
    entities = blueprint["entities"]

    assert sum(entity.get("name") == "assembling-machine-3" for entity in entities) == 1
    assert [seam.name for seam in component.seams] == ["control"]
    assert component.seam("control").anchors == (
        "inventory",
        "offer_valid",
        "blocked",
        "accepted",
        "busy_count",
        "completion_count",
        "reserved",
        "promised",
        "settling",
        "job_recipe",
    )

    encoded = encode_blueprint(blueprint)
    assert encoded.startswith("0")
    assert len(encoded) > 1_000
