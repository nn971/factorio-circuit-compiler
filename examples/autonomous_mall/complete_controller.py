"""Generate complete tileable autonomous-mall worker cells and the standard five-worker row."""

from __future__ import annotations

from copy import deepcopy
from math import hypot
from typing import Callable

from factorio_circuit import ModuleInterface, SignalId, compile_module

from examples.autonomous_mall.device_tiles import build_complete_book
from examples.autonomous_mall.manual_controller import (
    ASSEMBLER_DOCKS,
    HEAD_DOCKS,
    RECYCLER_DOCKS,
    CompiledTile,
    DockSpec,
    TILE_HEIGHT,
    TILE_WIDTH,
    _compose_controller,
    _decorate_tile,
    _placement,
    build_assembler_tile,
    build_head_tile,
    build_recycler_tile,
)

DISPATCH = SignalId("virtual", "signal-D")
LAUNCH = SignalId("virtual", "signal-L")
WORKING = SignalId("virtual", "signal-W")
FINISHED = SignalId("virtual", "signal-F")
EACH = SignalId("virtual", "signal-each")

# Final Factorio prototype names. device_tiles.py intentionally remains a composition layer; these
# aliases are normalized here until that layer grows a first-class entity-prototype abstraction.
_PROTOTYPE_NAME_FIXES = {
    "logistic-chest-requester": "requester-chest",
    "logistic-chest-passive-provider": "passive-provider-chest",
    "stack-inserter": "bulk-inserter",
}

# Complete cells need their configuration ports physically close to the machine bay. The semantic
# circuits are unchanged; only the public anchors differ from the controller-only diagnostic tiles.
COMPLETE_HEAD_INTERFACE = ModuleInterface(
    inputs={
        "stock": (8.0, 40.0),
        "control": (4.0, 40.0),
    },
    outputs={
        "available_out": (40.0, 6.0),
        "control_out": (40.0, 10.0),
        "frozen": (28.0, 2.0),
    },
    grid_size=(TILE_WIDTH, TILE_HEIGHT),
)

COMPLETE_ASSEMBLER_INTERFACE = ModuleInterface(
    inputs={
        "available_in": (0.0, 6.0),
        "control_in": (0.0, 10.0),
        "job_request": (4.0, 40.0),
        "job_recipe": (10.0, 40.0),
        "working": (24.0, 40.0),
        "finished": (30.0, 40.0),
    },
    outputs={
        "remaining_out": (40.0, 6.0),
        "control_out": (40.0, 10.0),
        "requester_demand": (8.0, 40.0),
        "recipe": (14.0, 40.0),
        "input_enable": (18.0, 40.0),
        "ack_finished": (36.0, 40.0),
        "accepted": (24.0, 2.0),
        "busy": (30.0, 2.0),
        "waiting_finished": (34.0, 2.0),
        "armed": (38.0, 2.0),
    },
    grid_size=(TILE_WIDTH, TILE_HEIGHT),
)

COMPLETE_RECYCLER_INTERFACE = ModuleInterface(
    inputs={
        "available_in": (0.0, 6.0),
        "control_in": (0.0, 10.0),
        "job_request": (4.0, 40.0),
        "working": (24.0, 40.0),
        "finished": (30.0, 40.0),
    },
    outputs={
        "remaining_out": (40.0, 6.0),
        "control_out": (40.0, 10.0),
        "requester_demand": (8.0, 40.0),
        "input_enable": (18.0, 40.0),
        "ack_finished": (36.0, 40.0),
        "accepted": (24.0, 2.0),
        "busy": (30.0, 2.0),
        "waiting_finished": (34.0, 2.0),
        "armed": (38.0, 2.0),
    },
    grid_size=(TILE_WIDTH, TILE_HEIGHT),
)

COMPLETE_HEAD_DOCKS = (
    DockSpec("control", "input", (4.0, 48.0), label="CONTROL D/L — EDIT HERE"),
    *HEAD_DOCKS,
)
COMPLETE_ASSEMBLER_DOCKS = (
    DockSpec("job_request", "input", (4.0, 48.0), label="AUTO ingredients — DO NOT EDIT"),
    DockSpec("job_recipe", "input", (8.0, 48.0), label="RECIPE COMMAND — EDIT HERE"),
    *ASSEMBLER_DOCKS,
)
COMPLETE_RECYCLER_DOCKS = (
    DockSpec("job_request", "input", (4.0, 48.0), label="RECYCLE ITEM — EDIT HERE"),
    *RECYCLER_DOCKS,
)


def _compile_complete_tiles() -> tuple[CompiledTile, CompiledTile, CompiledTile]:
    specs: tuple[
        tuple[
            str,
            Callable[[], object],
            ModuleInterface,
            tuple[DockSpec, ...],
        ],
        ...,
    ] = (
        ("HEAD tile", build_head_tile, COMPLETE_HEAD_INTERFACE, COMPLETE_HEAD_DOCKS),
        (
            "ASSEMBLER worker tile",
            build_assembler_tile,
            COMPLETE_ASSEMBLER_INTERFACE,
            COMPLETE_ASSEMBLER_DOCKS,
        ),
        (
            "RECYCLER worker tile",
            build_recycler_tile,
            COMPLETE_RECYCLER_INTERFACE,
            COMPLETE_RECYCLER_DOCKS,
        ),
    )
    result: list[CompiledTile] = []
    for label, builder, interface, docks in specs:
        circuit = builder()
        compiled = compile_module(circuit, interface, placement=_placement())
        result.append(CompiledTile(label, compiled, _decorate_tile(label, compiled, docks)))
    head, assembler, recycler = result
    return head, assembler, recycler


def _constant_sections(signals: tuple[tuple[SignalId, int], ...]) -> dict[str, object]:
    filters: list[dict[str, object]] = []
    for index, (signal, count) in enumerate(signals, start=1):
        item: dict[str, object] = {
            "index": index,
            "name": signal.name,
            "quality": "normal",
            "comparator": "=",
            "count": count,
        }
        if signal.kind:
            item["type"] = signal.kind
        filters.append(item)
    return {"sections": {"sections": [{"index": 1, "filters": filters}]}}


def _seed_head_controls(wrapper: dict[str, object]) -> None:
    blueprint = wrapper["blueprint"]
    assert isinstance(blueprint, dict)
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    matches = [
        entity
        for entity in entities
        if isinstance(entity, dict)
        and entity.get("player_description") == "DOCK CONTROL D/L — EDIT HERE"
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one complete HEAD control dock, got {len(matches)}")
    matches[0]["control_behavior"] = _constant_sections(((DISPATCH, 0), (LAUNCH, 0)))


def _description(entity: dict[str, object]) -> str:
    return str(entity.get("player_description", ""))


def _position(entity: dict[str, object]) -> tuple[float, float]:
    position = entity.get("position")
    if not isinstance(position, dict):
        raise ValueError(f"entity {entity.get('entity_number')} has no position")
    return float(position["x"]), float(position["y"])


def _set_position(entity: dict[str, object], x: float, y: float) -> None:
    entity["position"] = {"x": x, "y": y}


def _nearest_device_entity(
    entities: list[dict[str, object]],
    machine: dict[str, object],
    marker: str,
) -> dict[str, object]:
    mx, my = _position(machine)
    matches = [entity for entity in entities if marker in _description(entity)]
    if not matches:
        raise ValueError(f"missing device entity containing description {marker!r}")
    return min(
        matches,
        key=lambda entity: hypot(_position(entity)[0] - mx, _position(entity)[1] - my),
    )


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _red_arithmetic(
    entity_id: int,
    x: float,
    y: float,
    *,
    first: SignalId,
    multiplier: int,
    output: SignalId,
    description: str,
) -> dict[str, object]:
    return {
        "entity_number": entity_id,
        "name": "arithmetic-combinator",
        "position": {"x": x, "y": y},
        "direction": 4,
        "player_description": description,
        "control_behavior": {
            "arithmetic_conditions": {
                "operation": "*",
                "first_signal": _signal_json(first),
                "first_signal_networks": {"red": True, "green": False},
                "second_constant": multiplier,
                "output_signal": _signal_json(output),
            }
        },
    }


def _relay(entity_id: int, x: float, y: float, description: str) -> dict[str, object]:
    return {
        "entity_number": entity_id,
        "name": "constant-combinator",
        "position": {"x": x, "y": y},
        "player_description": description,
    }


def _normalized_wire(
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> tuple[int, int, int, int]:
    if left > right:
        return right, right_connector, left, left_connector
    return left, left_connector, right, right_connector


def _add_wire(
    wires: set[tuple[int, int, int, int]],
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> None:
    wires.add(_normalized_wire(left, left_connector, right, right_connector))


def _add_auto_ingredient_reader(
    blueprint: dict[str, object],
    entities: list[dict[str, object]],
    wires: set[tuple[int, int, int, int]],
    machine: dict[str, object],
    *,
    next_id: int,
) -> int:
    """Feed current recipe ingredients into the worker reservation input without W/F status lanes."""

    auto_dock = _nearest_device_entity(entities, machine, "DOCK AUTO ingredients — DO NOT EDIT")
    raw_relay = _nearest_device_entity(entities, machine, "machine status relay")
    request_relay = _nearest_device_entity(entities, machine, "requester demand relay")
    feeder = _nearest_device_entity(entities, machine, "MALL DEVICE feeder")
    recipe_command = _nearest_device_entity(entities, machine, "DOCK RECIPE COMMAND — EDIT HERE")
    recipe_bridge = _nearest_device_entity(entities, machine, "recipe red->green isolation")

    # The complete cell selects its recipe before dispatch. This makes Read ingredients available
    # continuously, so reservation can be decided before the transaction is launched.
    _add_wire(
        wires,
        int(recipe_command["entity_number"]),
        1,
        int(recipe_bridge["entity_number"]),
        1,
    )

    # Request exactly one recipe worth of ingredients. The requester output is already gated by the
    # worker START state, so no machine-content subtraction is needed for a one-craft transaction.
    _add_wire(
        wires,
        int(request_relay["entity_number"]),
        1,
        int(feeder["entity_number"]),
        1,
    )

    mx, _my = _position(machine)
    origin_x = round((mx - 17.5) / TILE_WIDTH) * TILE_WIDTH

    copy_each = next_id
    cancel_working = next_id + 1
    cancel_finished = next_id + 2
    merge = next_id + 3
    relay_a = next_id + 4
    relay_b = next_id + 5
    relay_c = next_id + 6
    relay_d = next_id + 7
    next_id += 8

    entities.extend(
        [
            _red_arithmetic(
                copy_each,
                origin_x + 29.0,
                62.0,
                first=EACH,
                multiplier=1,
                output=EACH,
                description="MALL DEVICE auto ingredients copy",
            ),
            _red_arithmetic(
                cancel_working,
                origin_x + 33.0,
                61.0,
                first=WORKING,
                multiplier=-1,
                output=WORKING,
                description="MALL DEVICE strip working from ingredients",
            ),
            _red_arithmetic(
                cancel_finished,
                origin_x + 31.0,
                65.0,
                first=FINISHED,
                multiplier=-1,
                output=FINISHED,
                description="MALL DEVICE strip finished from ingredients",
            ),
            _relay(merge, origin_x + 32.0, 68.0, "MALL DEVICE clean ingredient bus"),
            _relay(relay_a, origin_x + 24.0, 69.0, "MALL DEVICE ingredient relay A"),
            _relay(relay_b, origin_x + 16.0, 67.0, "MALL DEVICE ingredient relay B"),
            _relay(relay_c, origin_x + 9.0, 62.0, "MALL DEVICE ingredient relay C"),
            _relay(relay_d, origin_x + 4.0, 55.0, "MALL DEVICE ingredient relay D"),
        ]
    )

    source = int(raw_relay["entity_number"])
    _add_wire(wires, source, 1, copy_each, 1)
    _add_wire(wires, source, 1, cancel_working, 1)
    _add_wire(wires, source, 1, cancel_finished, 1)
    _add_wire(wires, copy_each, 3, merge, 1)
    _add_wire(wires, cancel_working, 3, merge, 1)
    _add_wire(wires, cancel_finished, 3, merge, 1)
    _add_wire(wires, merge, 1, relay_a, 1)
    _add_wire(wires, relay_a, 1, relay_b, 1)
    _add_wire(wires, relay_b, 1, relay_c, 1)
    _add_wire(wires, relay_c, 1, relay_d, 1)
    _add_wire(wires, relay_d, 1, int(auto_dock["entity_number"]), 1)
    return next_id


def _normalize_worker_devices(blueprint: dict[str, object]) -> None:
    """Apply Factorio prototype names, correct item flow, and automatic assembler ingredients."""

    raw_entities = blueprint.get("entities", [])
    if not isinstance(raw_entities, list):
        raise ValueError("blueprint entities must be a list")
    entities = [entity for entity in raw_entities if isinstance(entity, dict)]

    for entity in entities:
        name = entity.get("name")
        if isinstance(name, str) and name in _PROTOTYPE_NAME_FIXES:
            entity["name"] = _PROTOTYPE_NAME_FIXES[name]

    raw_wires = blueprint.setdefault("wires", [])
    if not isinstance(raw_wires, list):
        raise ValueError("blueprint wires must be a list")
    wires = {
        _normalized_wire(*(int(value) for value in wire))
        for wire in raw_wires
        if isinstance(wire, list) and len(wire) == 4
    }
    next_id = max((int(entity["entity_number"]) for entity in entities), default=0) + 1

    # Inserter direction is the pickup-facing direction. For left->right transfer, pickup is west
    # (12), not east. Assembling-machine-3 itself has no orientation field.
    assemblers = [entity for entity in entities if entity.get("name") == "assembling-machine-3"]
    for machine in assemblers:
        feeder = _nearest_device_entity(entities, machine, "MALL DEVICE feeder")
        output_inserter = _nearest_device_entity(entities, machine, "MALL DEVICE output inserter")
        feeder["direction"] = 12
        output_inserter["direction"] = 12
        machine.pop("direction", None)

        behavior = machine.get("control_behavior")
        if not isinstance(behavior, dict):
            raise ValueError("assembler is missing control behavior")
        behavior["read_contents"] = False
        behavior.pop("include_in_crafting", None)
        behavior["read_ingredients"] = True

        next_id = _add_auto_ingredient_reader(
            blueprint,
            entities,
            wires,
            machine,
            next_id=next_id,
        )

    # Recycler is 2x4 and north-facing. Its feeder sits south of the machine, so pickup is south
    # (direction 8) and drop is north. The recycler ejects directly into its north output chest.
    recycler_output_inserters: set[int] = set()
    for machine in [entity for entity in entities if entity.get("name") == "recycler"]:
        old_x, _old_y = _position(machine)
        tile_origin = round((old_x - 17.5) / TILE_WIDTH) * TILE_WIDTH
        center_x = tile_origin + 17.0
        center_y = 59.0

        requester = _nearest_device_entity(entities, machine, "MALL DEVICE requester")
        feeder = _nearest_device_entity(entities, machine, "MALL DEVICE feeder")
        provider = _nearest_device_entity(entities, machine, "MALL DEVICE output provider")
        output_inserter = _nearest_device_entity(entities, machine, "MALL DEVICE output inserter")

        _set_position(machine, center_x, center_y)
        machine["direction"] = 0
        _set_position(feeder, center_x - 0.5, center_y + 2.5)
        feeder["direction"] = 8
        _set_position(requester, center_x - 0.5, center_y + 3.5)
        _set_position(provider, center_x - 0.5, center_y - 2.5)
        recycler_output_inserters.add(int(output_inserter["entity_number"]))

        behavior = machine.get("control_behavior")
        if isinstance(behavior, dict):
            behavior["read_contents"] = False
            behavior.pop("include_in_crafting", None)
            behavior.pop("read_ingredients", None)

    if recycler_output_inserters:
        blueprint["entities"] = [
            entity
            for entity in raw_entities
            if not (
                isinstance(entity, dict)
                and int(entity.get("entity_number", -1)) in recycler_output_inserters
            )
        ]
        wires = {
            wire
            for wire in wires
            if wire[0] not in recycler_output_inserters and wire[2] not in recycler_output_inserters
        }

    blueprint["wires"] = [list(wire) for wire in sorted(wires)]


def _normalize_complete_book(payload: dict[str, object]) -> dict[str, object]:
    root = payload.get("blueprint_book")
    if not isinstance(root, dict):
        raise ValueError("expected blueprint-book payload")
    entries = root.get("blueprints")
    if not isinstance(entries, list):
        raise ValueError("blueprint book must contain a blueprint list")

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        blueprint = entry.get("blueprint")
        if isinstance(blueprint, dict):
            _normalize_worker_devices(blueprint)
    return payload


def build_complete_blueprint_book() -> dict[str, object]:
    """Compile complete-cell controller geometry once and attach physical devices."""

    head, assembler, recycler = _compile_complete_tiles()
    _seed_head_controls(head.blueprint)
    controller_only = _compose_controller(head, assembler, recycler)
    payload = build_complete_book(
        deepcopy(head.blueprint),
        deepcopy(assembler.blueprint),
        deepcopy(recycler.blueprint),
        controller_only,
    ).payload()
    return _normalize_complete_book(payload)


def main() -> None:
    from factorio_circuit.synthesis.interface import encode_blueprint_payload

    print(encode_blueprint_payload(build_complete_blueprint_book()))


if __name__ == "__main__":
    main()
