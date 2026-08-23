from examples.autonomous_mall.seamed_worker_pool import (
    build_dispatch_head,
    build_seamed_worker_pool_component,
    build_worker_stage,
)
from factorio_circuit import SignalId
from factorio_circuit.simulate.semantic import simulate_stream

GEAR = SignalId("item", "iron-gear-wheel")
PLATE = SignalId("item", "iron-plate")


def _trace(circuit, rows):
    module = circuit.build()
    values = simulate_stream(module, rows)
    return [dict(zip(module.output.names, row, strict=True)) for row in values]


def _combinator_footprint(entity):
    name = entity.get("name")
    if name not in {
        "constant-combinator",
        "arithmetic-combinator",
        "decider-combinator",
        "selector-combinator",
    }:
        return None
    x = float(entity["position"]["x"])
    y = float(entity["position"]["y"])
    if name == "constant-combinator":
        half_width = half_height = 0.5
    elif int(entity.get("direction", 0)) in {0, 4}:
        half_width, half_height = 0.5, 1.0
    else:
        half_width, half_height = 1.0, 0.5
    return (
        x - half_width,
        y - half_height,
        x + half_width,
        y + half_height,
    )


def _footprints_overlap(left, right) -> bool:
    return (
        left[0] < right[2] - 1e-9
        and right[0] < left[2] - 1e-9
        and left[1] < right[3] - 1e-9
        and right[1] < left[3] - 1e-9
    )


def _assert_anchor_adapters_clear(entities) -> None:
    adapters = [
        entity
        for entity in entities
        if str(entity.get("player_description", "")).startswith("ANCHOR ADAPTER")
    ]
    for adapter in adapters:
        adapter_box = _combinator_footprint(adapter)
        assert adapter_box is not None
        for other in entities:
            if other is adapter:
                continue
            other_box = _combinator_footprint(other)
            if other_box is None:
                continue
            assert not _footprints_overlap(adapter_box, other_box), (
                f"{adapter.get('player_description')} at {adapter['position']} overlaps "
                f"{other.get('player_description', other.get('name'))} at {other['position']}"
            )


def test_dispatch_head_publishes_packet_before_probe_then_retries() -> None:
    base = {
        "offer_valid": 1,
        "offer_recipe": {GEAR: 1},
        "offer_inputs": {PLATE: 2},
        "offer_product": {GEAR: 1},
        "bus_blocked": 0,
        "bus_accepted": 0,
        "bus_busy_count": 1,
        "bus_completion_count": 0,
        "bus_reserved": {PLATE: 2},
        "bus_promised": {GEAR: 1},
    }
    rows = [
        dict(base),
        dict(base),
        {**base, "bus_blocked": 1},
        dict(base),
        dict(base),
    ]

    trace = _trace(build_dispatch_head(), rows)

    # The payload is published immediately, but the probe is armed through state and cannot launch
    # until the next logical reaction.  This head start prevents physical lane skew from dropping
    # the reservation vector while recipe/product have already arrived.
    assert trace[0]["bus_offer_valid"] == 0
    assert trace[0]["bus_offer_recipe"] == {GEAR: 1}
    assert trace[0]["bus_offer_inputs"] == {PLATE: 2}
    assert trace[0]["bus_offer_product"] == {GEAR: 1}

    assert trace[1]["bus_offer_valid"] == 1
    assert trace[1]["bus_offer_inputs"] == {PLATE: 2}
    assert trace[2]["bus_offer_valid"] == 0
    assert trace[2]["offer_blocked"] == 1
    # The packet remains continuously published, so retry only needs another scalar probe.
    assert trace[2]["bus_offer_inputs"] == {PLATE: 2}
    assert trace[3]["bus_offer_valid"] == 1
    assert trace[4]["bus_offer_valid"] == 0


def test_dispatch_head_stops_probing_after_accept_until_valid_drops() -> None:
    base = {
        "offer_valid": 1,
        "offer_recipe": {GEAR: 1},
        "offer_inputs": {PLATE: 2},
        "offer_product": {GEAR: 1},
        "bus_blocked": 0,
        "bus_accepted": 0,
        "bus_busy_count": 0,
        "bus_completion_count": 0,
        "bus_reserved": {},
        "bus_promised": {},
    }
    rows = [
        dict(base),
        dict(base),
        {**base, "bus_accepted": 1},
        dict(base),
        {**base, "offer_valid": 0},
        {**base, "offer_valid": 0},
        dict(base),
        dict(base),
    ]

    trace = _trace(build_dispatch_head(), rows)

    assert trace[0]["bus_offer_valid"] == 0
    assert trace[1]["bus_offer_valid"] == 1
    assert trace[2]["offer_accepted"] == 1
    assert all(trace[index]["bus_offer_valid"] == 0 for index in (2, 3, 4, 5, 6))
    # Raising V after the full low phase republishes first, then probes one reaction later.
    assert trace[6]["bus_offer_inputs"] == {PLATE: 2}
    assert trace[7]["bus_offer_valid"] == 1


def test_idle_worker_consumes_probe_and_busy_worker_forwards_it() -> None:
    idle_rows = [
        {
            "in_offer_valid": 1,
            "in_offer_recipe": {GEAR: 1},
            "in_offer_inputs": {PLATE: 2},
            "in_offer_product": {GEAR: 1},
            "down_blocked": 0,
            "down_accepted": 0,
            "down_busy_count": 0,
            "down_completion_count": 0,
            "down_reserved": {},
            "down_promised": {},
            "device_working": 0,
        }
    ]
    idle = _trace(build_worker_stage(), idle_rows)[0]
    assert idle["up_accepted"] == 1
    assert idle["next_offer_valid"] == 0
    assert idle["up_reserved"] == {PLATE: 2}
    assert idle["up_promised"] == {GEAR: 1}
    assert idle["up_busy_count"] == 1

    # Payload forwarding is independent of probe forwarding.  Even the idle worker that consumes
    # the token leaves the stable packet visible downstream; without a token, no later worker may
    # claim it.
    assert idle["next_offer_recipe"] == {GEAR: 1}
    assert idle["next_offer_inputs"] == {PLATE: 2}
    assert idle["next_offer_product"] == {GEAR: 1}

    rows = idle_rows + [
        {
            **idle_rows[0],
            "in_offer_valid": 0,
        },
        {
            **idle_rows[0],
            "in_offer_valid": 1,
        },
    ]
    packet_only = _trace(build_worker_stage(), rows)[1]
    assert packet_only["next_offer_valid"] == 0
    assert packet_only["next_offer_inputs"] == {PLATE: 2}

    busy = _trace(build_worker_stage(), rows)[2]
    assert busy["up_accepted"] == 0
    assert busy["next_offer_valid"] == 1
    assert busy["next_offer_recipe"] == {GEAR: 1}
    assert busy["next_offer_inputs"] == {PLATE: 2}


def test_two_worker_seamed_component_is_bounded_and_contains_real_devices() -> None:
    component = build_seamed_worker_pool_component(2)
    blueprint = component.anchored.blueprint
    entities = blueprint["entities"]
    wires = blueprint["wires"]

    assert [seam.name for seam in component.seams] == ["external"]
    # Each compiled head/worker controller owns a dense body plus a west routing strip. Each worker
    # also owns one assembler footprint; the manual tail owns one footprint.
    assert len(component.footprints) == 9
    assert sum(entity.get("name") == "assembling-machine-3" for entity in entities) == 2
    assert sum(entity.get("name") == "requester-chest" for entity in entities) == 2
    assert sum(entity.get("name") == "active-provider-chest" for entity in entities) == 2

    descriptions = [str(entity.get("player_description", "")) for entity in entities]
    assert not any(description.startswith("ANCHOR RELAY") for description in descriptions)

    # Post-compilation interface adapters must be physically placeable.  This specifically catches
    # router relays occupying an adapter's 1x2 footprint, which Factorio resolves by dropping the
    # adapter and disconnecting that seam lane.
    _assert_anchor_adapters_clear(entities)

    # The public ABI must be a literal exposed top seam, not metadata attached to terminals buried
    # in the annealed body.  Only ANCHOR-owned adapter infrastructure may occupy the four-tile strip
    # immediately beneath it; the compiler markers sit exactly at the strip's inner edge.
    external = component.seam("external")
    external_anchors = [component.anchored.anchor(name) for name in external.anchors]
    external_y = external_anchors[0].position[1]
    assert all(anchor.position[1] == external_y for anchor in external_anchors)
    assert external_y == min(float(entity["position"]["y"]) for entity in entities)

    corridor_entities = [
        entity
        for entity in entities
        if external_y <= float(entity["position"]["y"]) < external_y + 4.0
    ]
    assert corridor_entities
    assert all(
        str(entity.get("player_description", "")).startswith("ANCHOR")
        for entity in corridor_entities
    )

    # Every surviving public dock must have a real incident circuit wire in the final composed
    # blueprint.  This catches visually orphaned terminals such as the previous offer_valid bug.
    normalized_wires = [tuple(int(value) for value in wire) for wire in wires]
    for anchor in external_anchors:
        assert any(
            (wire[0] == anchor.entity_number and wire[1] == anchor.connector_id)
            or (wire[2] == anchor.entity_number and wire[3] == anchor.connector_id)
            for wire in normalized_wires
        )

    assert component.anchored.anchor("offer_valid").position[1] == external_y
    assert component.anchored.anchor("offer_recipe").position[1] == external_y
