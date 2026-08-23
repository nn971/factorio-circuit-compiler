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


def test_dispatch_head_sends_one_probe_then_waits_for_response() -> None:
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

    assert trace[0]["bus_offer_valid"] == 1
    assert trace[0]["bus_offer_recipe"] == {GEAR: 1}
    assert trace[1]["bus_offer_valid"] == 0
    assert trace[2]["bus_offer_valid"] == 0
    assert trace[2]["offer_blocked"] == 1
    # The block response clears the waiting latch; the held external offer is retried next reaction.
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
        {**base, "bus_accepted": 1},
        dict(base),
        dict(base),
        {**base, "offer_valid": 0},
        {**base, "offer_valid": 0},
        dict(base),
    ]

    trace = _trace(build_dispatch_head(), rows)

    assert trace[0]["bus_offer_valid"] == 1
    assert trace[1]["offer_accepted"] == 1
    assert all(trace[index]["bus_offer_valid"] == 0 for index in (1, 2, 3, 4, 5))
    assert trace[6]["bus_offer_valid"] == 1


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
    busy = _trace(build_worker_stage(), rows)[2]
    assert busy["up_accepted"] == 0
    assert busy["next_offer_valid"] == 1
    assert busy["next_offer_recipe"] == {GEAR: 1}


def test_two_worker_seamed_component_is_bounded_and_contains_real_devices() -> None:
    component = build_seamed_worker_pool_component(2)
    blueprint = component.anchored.blueprint
    entities = blueprint["entities"]

    assert [seam.name for seam in component.seams] == ["external"]
    assert len(component.footprints) == 6  # head + 2*(controller+assembler) + tail
    assert sum(entity.get("name") == "assembling-machine-3" for entity in entities) == 2
    assert sum(entity.get("name") == "requester-chest" for entity in entities) == 2
    assert sum(entity.get("name") == "active-provider-chest" for entity in entities) == 2

    descriptions = [str(entity.get("player_description", "")) for entity in entities]
    assert not any(description.startswith("ANCHOR RELAY") for description in descriptions)
