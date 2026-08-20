from __future__ import annotations

from math import hypot

from examples.autonomous_mall.anchored_worker import (
    DISPATCH,
    LAUNCH,
    build_anchored_worker_device,
    build_assembler_worker,
    compile_assembler_worker,
    worker_as_anchored_blueprint,
)
from examples.autonomous_mall.anchored_worker_probe import build_probe_blueprint
from factorio_circuit.devices import AssemblerDevice, socketize_assembler_device
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.simulate.semantic import simulate_stream

IRON_PLATE = SignalId("item", "iron-plate")
IRON_GEAR = SignalId("recipe", "iron-gear-wheel")


def _records(module, rows):
    names = module.output.names
    assert all(name is not None for name in names)
    return [dict(zip(names, row, strict=True)) for row in rows]


def test_worker_level_state_machine_requests_then_runs_exactly_once() -> None:
    module = build_assembler_worker().build()
    stream: list[dict[str, object]] = []
    for tick in range(140):
        control: dict[SignalId, int] = {}
        if tick >= 5:
            control[DISPATCH] = 1
        if tick >= 15:
            control[LAUNCH] = 1
        stream.append(
            {
                "available_in": {IRON_PLATE: 100},
                "control_in": control,
                "job_recipe": {IRON_GEAR: 1},
                "ingredients": {IRON_PLATE: 2},
                "requester_contents": {IRON_PLATE: 2} if tick >= 45 else {},
                "working": 1 if 80 <= tick < 100 else 0,
            }
        )

    records = _records(module, simulate_stream(module, stream))
    assert any(int(record["accepted"]) != 0 for record in records[5:])

    demand_ticks = [
        tick
        for tick, record in enumerate(records)
        if isinstance(record["requester_demand"], dict)
        and record["requester_demand"].get(IRON_PLATE) == 2
    ]
    assert demand_ticks
    assert min(demand_ticks) >= 15
    assert max(demand_ticks) < 80

    enabled_ticks = [
        tick for tick, record in enumerate(records) if int(record["enable"]) != 0
    ]
    assert enabled_ticks
    assert min(enabled_ticks) > min(demand_ticks)
    assert any(int(record["waiting"]) != 0 for record in records[80:110])

    # Once working has risen and then fallen the transaction is over.  Held D/L must not launch a
    # second request; dropping D is the only re-arm condition.
    for record in records[110:]:
        assert int(record["busy"]) == 0
        assert int(record["enable"]) == 0
        assert record["requester_demand"] == {}


def test_socketized_assembler_exports_one_top_edge_protocol_row() -> None:
    socketed = socketize_assembler_device(AssemblerDevice().build())
    assert socketed.protocol.name == "assembler-v3-top-socket"
    assert len(socketed.ports) == 8
    assert {port.name for port in socketed.ports} == {
        "recipe",
        "enable",
        "requester_demand",
        "ingredients",
        "requester_contents",
        "provider_contents",
        "working",
        "finished",
    }
    positions = [port.endpoint.position for port in socketed.ports]
    assert len(set(positions)) == len(positions)
    assert {position[1] for position in positions} == {1.5}


def test_compiled_worker_binds_six_device_ports_by_exact_overlap() -> None:
    result = compile_assembler_worker()
    worker = worker_as_anchored_blueprint(result)
    device = socketize_assembler_device(AssemblerDevice().build()).anchored()
    composed = build_anchored_worker_device()

    assert {anchor.name for anchor in worker.anchors} == {
        "recipe",
        "enable",
        "requester_demand",
        "ingredients",
        "requester_contents",
        "working",
    }
    assert {anchor.name for anchor in composed.anchors} == {"provider_contents", "finished"}
    assert len(composed.blueprint["entities"]) == (
        len(worker.blueprint["entities"]) + len(device.blueprint["entities"]) - 6
    )


def test_composed_worker_device_has_legal_wire_reach_and_no_entity_overlap() -> None:
    composed = build_anchored_worker_device(
        modules=("productivity-module-3",) * 4,
    ).blueprint
    entities = composed["entities"]
    wires = composed.get("wires", [])
    by_id = {int(entity["entity_number"]): entity for entity in entities}

    def position(entity_id: int) -> tuple[float, float]:
        raw = by_id[entity_id]["position"]
        return float(raw["x"]), float(raw["y"])

    for left, _left_connector, right, _right_connector in wires:
        lx, ly = position(int(left))
        rx, ry = position(int(right))
        assert hypot(lx - rx, ly - ry) <= 9.0 + 1e-9

    def half_size(entity: dict[str, object]) -> tuple[float, float]:
        name = str(entity["name"])
        if name == "assembling-machine-3":
            return 1.5, 1.5
        if name in {"arithmetic-combinator", "decider-combinator"}:
            return 1.0, 0.5
        return 0.5, 0.5

    rectangles: list[tuple[int, float, float, float, float]] = []
    for entity in entities:
        entity_id = int(entity["entity_number"])
        x, y = position(entity_id)
        hx, hy = half_size(entity)
        rectangles.append((entity_id, x - hx, x + hx, y - hy, y + hy))

    for index, left in enumerate(rectangles):
        for right in rectangles[index + 1 :]:
            # Touching edges are legal; positive-area overlap is not.
            x_overlap = min(left[2], right[2]) - max(left[1], right[1])
            y_overlap = min(left[4], right[4]) - max(left[3], right[3])
            assert not (x_overlap > 1e-9 and y_overlap > 1e-9), (left[0], right[0])


def test_probe_seeds_stock_recipe_controls_and_active_provider() -> None:
    blueprint = build_probe_blueprint()
    entities = blueprint["entities"]
    descriptions = {str(entity.get("player_description", "")): entity for entity in entities}
    control = descriptions["PROBE CONTROL D/L — EDIT HERE"]
    filters = control["control_behavior"]["sections"]["sections"][0]["filters"]
    by_name = {item["name"]: item["count"] for item in filters}
    assert by_name == {DISPATCH.name: 0, LAUNCH.name: 0}

    recipe = descriptions["PROBE RECIPE iron-gear-wheel — fixed"]
    recipe_filters = recipe["control_behavior"]["sections"]["sections"][0]["filters"]
    assert [(item["name"], item["count"]) for item in recipe_filters] == [
        (IRON_GEAR.name, 1)
    ]
    assert sum(entity["name"] == "active-provider-chest" for entity in entities) == 1
