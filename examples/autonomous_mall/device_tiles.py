"""Blueprint composition for complete autonomous-mall production cells.

This module deliberately lives under ``examples/``. The compiler produces the controller circuit;
this layer appends ordinary Factorio machines, logistic chests, inserters, and a small device harness.
The only public electrical ABI that remains between independently pasted cells is the horizontal
available/control bus already exposed by ``manual_controller.py``.

The generated v1 cells support solid-ingredient recipes only. The feeder computes
``positive(requester_demand - machine_contents)`` and feeds one item at a time until the machine starts.
Recipes where one item is simultaneously an ingredient and a product remain out of scope because the
machine contents signal does not distinguish those roles.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable, Sequence

from factorio_circuit.ir.physical import SignalId
from factorio_circuit.synthesis.interface import encode_blueprint_payload

TILE_WIDTH = 48
COMPLETE_TILE_HEIGHT = 72
FACTORIO_BLUEPRINT_VERSION = 562949955518464

EACH = SignalId("virtual", "signal-each")
WORKING = SignalId("virtual", "signal-W")
FINISHED = SignalId("virtual", "signal-F")
INPUT_ENABLE = SignalId("virtual", "signal-I")
ACK_FINISHED = SignalId("virtual", "signal-A")
FEED_ENABLE = SignalId("virtual", "signal-E")

# Factorio 2.1 defines.inventory.crafter_modules. The old assembling-machine/furnace module
# inventories were folded into this common crafter inventory while retaining index 4.
CRAFTER_MODULES_INVENTORY = 4


@dataclass(frozen=True, slots=True)
class WorkerDeviceSpec:
    """Physical role attached beneath one already-compiled worker controller."""

    label: str
    machine_name: str
    module_name: str
    module_count: int = 4
    recipe_command: bool = True


PRODUCTIVITY_DEVICE = WorkerDeviceSpec(
    label="P productivity worker",
    machine_name="assembling-machine-3",
    module_name="productivity-module-3",
)
QUALITY_DEVICE = WorkerDeviceSpec(
    label="Q quality worker",
    machine_name="assembling-machine-3",
    module_name="quality-module-3",
)
RECYCLER_DEVICE = WorkerDeviceSpec(
    label="R recycler worker",
    machine_name="recycler",
    module_name="quality-module-3",
    recipe_command=False,
)


@dataclass(frozen=True, slots=True)
class CompleteMallBook:
    """Materialized reusable cells plus the preassembled five-worker row."""

    head: dict[str, object]
    productivity: dict[str, object]
    quality: dict[str, object]
    recycler: dict[str, object]
    assembled: dict[str, object]
    controller_only: dict[str, object]

    def payload(self) -> dict[str, object]:
        entries = [
            {"index": 0, **deepcopy(self.assembled)},
            {"index": 1, **deepcopy(self.productivity)},
            {"index": 2, **deepcopy(self.quality)},
            {"index": 3, **deepcopy(self.recycler)},
            {"index": 4, **deepcopy(self.controller_only)},
            {"index": 5, **deepcopy(self.head)},
        ]
        return {
            "blueprint_book": {
                "item": "blueprint-book",
                "label": "Autonomous mall complete worker cells",
                "active_index": 0,
                "version": FACTORIO_BLUEPRINT_VERSION,
                "blueprints": entries,
            }
        }

    def blueprint_string(self) -> str:
        return encode_blueprint_payload(self.payload())


def extend_head_tile(wrapper: dict[str, object]) -> dict[str, object]:
    """Put the proven HEAD controller on the same 48x72 grid as complete worker cells."""

    result = deepcopy(wrapper)
    blueprint = _blueprint(result)
    blueprint["label"] = "HEAD complete-row tile"
    blueprint["snap-to-grid"] = {"x": TILE_WIDTH, "y": COMPLETE_TILE_HEIGHT}
    blueprint["absolute-snapping"] = True
    blueprint["position-relative-to-grid"] = {"x": 0, "y": 0}
    _validate_bounds(blueprint)
    return result


def attach_worker_device(
    wrapper: dict[str, object],
    spec: WorkerDeviceSpec,
) -> dict[str, object]:
    """Append a complete machine bay beneath a decorated worker controller blueprint."""

    result = deepcopy(wrapper)
    blueprint = _blueprint(result)
    blueprint["label"] = spec.label
    blueprint["snap-to-grid"] = {"x": TILE_WIDTH, "y": COMPLETE_TILE_HEIGHT}
    blueprint["absolute-snapping"] = True
    blueprint["position-relative-to-grid"] = {"x": 0, "y": 0}

    entities = _entities(blueprint)
    wires = _wires(blueprint)
    next_id = max((int(entity["entity_number"]) for entity in entities), default=0) + 1

    requester_dock = _dock_id(entities, "requester demand")
    input_enable_dock = _dock_id(entities, "input enable")
    working_dock = _dock_id(entities, "working")
    finished_dock = _dock_id(entities, "finished")
    ack_dock = _dock_id(entities, "finish acknowledgement")
    recipe_dock = _dock_id(entities, "recipe") if spec.recipe_command else None

    # Item-moving entities. The horizontal arrangement keeps the machine bay compact and leaves
    # room around it for the generated control combinators.
    requester = next_id
    feeder = next_id + 1
    machine = next_id + 2
    output_inserter = next_id + 3
    provider = next_id + 4
    next_id += 5
    entities.extend(
        [
            {
                "entity_number": requester,
                "name": "logistic-chest-requester",
                "position": {"x": 14.5, "y": 60.5},
                "player_description": "MALL DEVICE requester — one-craft demand",
                "control_behavior": {
                    "set_requests": True,
                    "read_contents": False,
                    "input_networks": _networks(red=True, green=False),
                    "output_networks": _networks(red=False, green=False),
                },
            },
            {
                "entity_number": feeder,
                "name": "stack-inserter",
                "position": {"x": 15.5, "y": 60.5},
                "direction": 4,
                "override_stack_size": 1,
                "player_description": (
                    "MALL DEVICE feeder — missing solid ingredients; stack-size override 1"
                ),
                "control_behavior": {
                    "input_networks": _networks(red=True, green=False),
                    "output_networks": _networks(red=False, green=False),
                    "circuit_set_filters": True,
                    "circuit_enabled": True,
                    "circuit_condition": {
                        "first_signal": _signal_json(FEED_ENABLE),
                        "constant": 0,
                        "comparator": ">",
                    },
                },
            },
            _machine_entity(machine, spec),
            {
                "entity_number": output_inserter,
                "name": "stack-inserter",
                "position": {"x": 19.5, "y": 60.5},
                "direction": 4,
                "player_description": "MALL DEVICE output inserter",
            },
            {
                "entity_number": provider,
                "name": "logistic-chest-passive-provider",
                "position": {"x": 20.5, "y": 60.5},
                "player_description": "MALL DEVICE output provider",
            },
        ]
    )

    # Requester-demand fanout and the positive side of request - machine_contents.
    request_relay = next_id
    request_bridge = next_id + 1
    request_sum_relay_a = next_id + 2
    request_sum_relay_b = next_id + 3
    next_id += 4
    entities.extend(
        [
            _relay(request_relay, 12.0, 55.0, "requester demand relay"),
            _relay(request_bridge, 10.0, 60.0, "request subtraction bridge"),
            _relay(request_sum_relay_a, 15.0, 64.0, "request subtraction relay A"),
            _relay(request_sum_relay_b, 22.0, 64.0, "request subtraction relay B"),
        ]
    )
    _wire(wires, requester_dock, 1, request_relay, 1)
    _wire(wires, request_relay, 1, requester, 1)
    _wire(wires, request_relay, 1, request_bridge, 1)
    _wire(wires, request_bridge, 1, request_sum_relay_a, 1)
    _wire(wires, request_sum_relay_a, 1, request_sum_relay_b, 1)

    # Assembler recipe is bridged to GREEN so RED machine status/contents cannot feed Set recipe.
    if recipe_dock is not None:
        recipe_bridge = next_id
        next_id += 1
        entities.append(
            _arithmetic(
                recipe_bridge,
                18.0,
                54.0,
                operation="*",
                first=EACH,
                second_constant=1,
                output=EACH,
                first_network="red",
                description="MALL DEVICE recipe red->green isolation",
            )
        )
        _wire(wires, recipe_dock, 1, recipe_bridge, 1)
        _wire(wires, recipe_bridge, 4, machine, 2)

    # Machine RED output contains contents plus the fixed W/F status lanes.
    machine_relay = next_id
    machine_raw_relay = next_id + 1
    working_relay = next_id + 2
    next_id += 3
    entities.extend(
        [
            _relay(machine_relay, 23.0, 58.0, "machine contents/status relay"),
            _relay(machine_raw_relay, 27.0, 57.0, "machine status relay"),
            _relay(working_relay, 25.0, 53.0, "working-status relay"),
        ]
    )
    _wire(wires, machine, 1, machine_relay, 1)
    _wire(wires, machine_relay, 1, machine_raw_relay, 1)
    _wire(wires, machine_raw_relay, 1, working_relay, 1)
    _wire(wires, working_relay, 1, working_dock, 1)

    # General solid-ingredient feeder:
    #     missing = positive(requester_demand - machine_contents)
    # The negative path also sees W/F virtual lanes; circuit-set inserter filters ignore non-items.
    negate_contents = next_id
    positive_missing = next_id + 1
    filter_relay = next_id + 2
    next_id += 3
    entities.extend(
        [
            _arithmetic(
                negate_contents,
                26.0,
                61.0,
                operation="*",
                first=EACH,
                second_constant=-1,
                output=EACH,
                first_network="red",
                description="MALL DEVICE negate machine contents",
            ),
            _decider(
                positive_missing,
                25.0,
                67.0,
                conditions=[_condition(EACH, ">", constant=0, network="red")],
                output=EACH,
                copy_count=True,
                description="MALL DEVICE positive missing ingredients -> inserter filters",
            ),
            _relay(filter_relay, 19.0, 65.0, "missing-ingredient filter relay"),
        ]
    )
    _wire(wires, machine_raw_relay, 1, negate_contents, 1)
    _wire(wires, negate_contents, 3, request_sum_relay_b, 1)
    _wire(wires, request_sum_relay_b, 1, positive_missing, 1)
    _wire(wires, positive_missing, 3, filter_relay, 1)
    _wire(wires, filter_relay, 1, feeder, 1)

    # Feeder is enabled only during START and before the machine reports working.
    feed_gate = next_id
    feed_gate_relay = next_id + 1
    next_id += 2
    entities.extend(
        [
            _decider(
                feed_gate,
                22.0,
                54.0,
                conditions=[
                    _condition(INPUT_ENABLE, ">", constant=0, network="red"),
                    _condition(WORKING, "=", constant=0, network="red", compare_type="and"),
                ],
                output=FEED_ENABLE,
                description="MALL DEVICE input-enable AND not-working",
            ),
            _relay(feed_gate_relay, 18.0, 56.0, "feeder-enable relay"),
        ]
    )
    _wire(wires, input_enable_dock, 1, feed_gate, 1)
    _wire(wires, working_relay, 1, feed_gate, 1)
    _wire(wires, feed_gate, 3, feed_gate_relay, 1)
    _wire(wires, feed_gate_relay, 1, feeder, 1)

    # Durable completion latch. SET catches the machine's one-tick F pulse. HOLD feeds F back until
    # acknowledgement A; the held F, not the raw pulse, drives the controller input.
    completion_set = next_id
    completion_hold = next_id + 1
    completion_latch = next_id + 2
    ack_relay = next_id + 3
    entities.extend(
        [
            _decider(
                completion_set,
                30.0,
                54.0,
                conditions=[_condition(FINISHED, ">", constant=0, network="red")],
                output=FINISHED,
                description="MALL DEVICE completion SET",
            ),
            _decider(
                completion_hold,
                36.0,
                54.0,
                conditions=[
                    _condition(FINISHED, ">", constant=0, network="red"),
                    _condition(
                        ACK_FINISHED,
                        "=",
                        constant=0,
                        network="red",
                        compare_type="and",
                    ),
                ],
                output=FINISHED,
                description="MALL DEVICE completion HOLD until acknowledgement",
            ),
            _relay(completion_latch, 33.0, 54.0, "completion latch bus"),
            _relay(ack_relay, 39.0, 52.0, "completion acknowledgement relay"),
        ]
    )
    _wire(wires, machine_raw_relay, 1, completion_set, 1)
    _wire(wires, completion_set, 3, completion_latch, 1)
    _wire(wires, completion_latch, 1, completion_hold, 1)
    _wire(wires, completion_hold, 3, completion_latch, 1)
    _wire(wires, completion_latch, 1, finished_dock, 1)
    _wire(wires, ack_dock, 1, ack_relay, 1)
    _wire(wires, ack_relay, 1, completion_hold, 1)

    blueprint["wires"] = [list(item) for item in sorted({_wire_tuple(wire) for wire in wires})]
    _validate_wire_references(blueprint)
    _validate_bounds(blueprint)
    return result


def compose_row(
    tiles: Sequence[tuple[str, dict[str, object]]],
    *,
    label: str,
) -> dict[str, object]:
    """Compose snap-compatible 48-wide cells and merge exact-overlap dock markers at seams."""

    entities: list[dict[str, object]] = []
    wires: set[tuple[int, int, int, int]] = set()
    shared_docks: dict[tuple[str, float, float], int] = {}
    next_id = 1

    for tile_index, (instance_label, wrapper) in enumerate(tiles):
        blueprint = _blueprint(wrapper)
        local_entities = _entities(blueprint)
        local_wires = _wires(blueprint)
        offset_x = tile_index * TILE_WIDTH
        id_map: dict[int, int] = {}

        for raw in local_entities:
            item = deepcopy(raw)
            local_id = int(item["entity_number"])
            position = item["position"]
            assert isinstance(position, dict)
            position["x"] = float(position["x"]) + offset_x
            x = float(position["x"])
            y = float(position["y"])
            description = str(item.get("player_description", ""))
            is_dock = item.get("name") == "constant-combinator" and description.startswith("DOCK ")
            key = (str(item.get("name")), x, y)

            if is_dock and key in shared_docks:
                id_map[local_id] = shared_docks[key]
                continue

            global_id = next_id
            next_id += 1
            id_map[local_id] = global_id
            item["entity_number"] = global_id
            if description and not is_dock:
                item["player_description"] = f"{instance_label}: {description}"
            entities.append(item)
            if is_dock:
                shared_docks[key] = global_id

        for raw_wire in local_wires:
            left, left_connector, right, right_connector = _wire_tuple(raw_wire)
            global_left = id_map[left]
            global_right = id_map[right]
            if global_left == global_right:
                continue
            wires.add(_normalized_wire(global_left, left_connector, global_right, right_connector))

    result: dict[str, object] = {
        "blueprint": {
            "item": "blueprint",
            "label": label,
            "version": FACTORIO_BLUEPRINT_VERSION,
            "entities": entities,
            "wires": [list(item) for item in sorted(wires)],
        }
    }
    _validate_wire_references(_blueprint(result))
    return result


def build_complete_book(
    head_controller: dict[str, object],
    assembler_controller: dict[str, object],
    recycler_controller: dict[str, object],
    controller_only: dict[str, object],
) -> CompleteMallBook:
    """Materialize reusable P/Q/R production cells and the standard five-worker row."""

    head = extend_head_tile(head_controller)
    productivity = attach_worker_device(assembler_controller, PRODUCTIVITY_DEVICE)
    quality = attach_worker_device(assembler_controller, QUALITY_DEVICE)
    recycler = attach_worker_device(recycler_controller, RECYCLER_DEVICE)
    assembled = compose_row(
        (
            ("HEAD", head),
            ("P0", productivity),
            ("P1", productivity),
            ("Q0", quality),
            ("Q1", quality),
            ("R0", recycler),
        ),
        label="Autonomous mall complete row — HEAD P0 P1 Q0 Q1 R0",
    )
    return CompleteMallBook(
        head=head,
        productivity=productivity,
        quality=quality,
        recycler=recycler,
        assembled=assembled,
        controller_only=deepcopy(controller_only),
    )


def _machine_entity(entity_id: int, spec: WorkerDeviceSpec) -> dict[str, object]:
    behavior: dict[str, object] = {
        "input_networks": _networks(red=False, green=spec.recipe_command),
        "output_networks": _networks(red=True, green=False),
        "read_contents": True,
        "include_in_crafting": True,
        "read_recipe_finished": True,
        "recipe_finished_signal": _signal_json(FINISHED),
        "read_working": True,
        "working_signal": _signal_json(WORKING),
    }
    if spec.recipe_command:
        behavior["set_recipe"] = True

    return {
        "entity_number": entity_id,
        "name": spec.machine_name,
        "position": {"x": 17.5, "y": 60.5},
        "player_description": f"MALL DEVICE {spec.label}",
        "items": [_module_insert_plan(spec.module_name, spec.module_count)],
        "control_behavior": behavior,
    }


def _module_insert_plan(module_name: str, count: int) -> dict[str, object]:
    return {
        "id": {"name": module_name, "quality": "normal"},
        "items": {
            "in_inventory": [
                {"inventory": CRAFTER_MODULES_INVENTORY, "stack": index, "count": 1}
                for index in range(count)
            ]
        },
    }


def _relay(entity_id: int, x: float, y: float, description: str) -> dict[str, object]:
    return {
        "entity_number": entity_id,
        "name": "constant-combinator",
        "position": {"x": x, "y": y},
        "player_description": f"MALL DEVICE {description}",
    }


def _arithmetic(
    entity_id: int,
    x: float,
    y: float,
    *,
    operation: str,
    first: SignalId,
    second_constant: int,
    output: SignalId,
    first_network: str,
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
                "operation": operation,
                "first_signal": _signal_json(first),
                "first_signal_networks": _networks(
                    red=first_network == "red",
                    green=first_network == "green",
                ),
                "second_constant": second_constant,
                "output_signal": _signal_json(output),
            }
        },
    }


def _decider(
    entity_id: int,
    x: float,
    y: float,
    *,
    conditions: Sequence[dict[str, object]],
    output: SignalId,
    copy_count: bool = False,
    description: str,
) -> dict[str, object]:
    output_spec: dict[str, object] = {
        "signal": _signal_json(output),
        "copy_count_from_input": copy_count,
    }
    return {
        "entity_number": entity_id,
        "name": "decider-combinator",
        "position": {"x": x, "y": y},
        "direction": 4,
        "player_description": description,
        "control_behavior": {
            "decider_conditions": {
                "conditions": list(conditions),
                "outputs": [output_spec],
            }
        },
    }


def _condition(
    signal: SignalId,
    comparator: str,
    *,
    constant: int,
    network: str,
    compare_type: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "first_signal": _signal_json(signal),
        "first_signal_networks": _networks(
            red=network == "red",
            green=network == "green",
        ),
        "constant": constant,
        "comparator": comparator,
    }
    if compare_type is not None:
        result["compare_type"] = compare_type
    return result


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _networks(*, red: bool, green: bool) -> dict[str, bool]:
    return {"red": red, "green": green}


def _blueprint(wrapper: dict[str, object]) -> dict[str, object]:
    blueprint = wrapper.get("blueprint")
    if not isinstance(blueprint, dict):
        raise ValueError("expected a single-blueprint wrapper")
    return blueprint


def _entities(blueprint: dict[str, object]) -> list[dict[str, object]]:
    raw = blueprint.setdefault("entities", [])
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("blueprint entities must be dictionaries")
    return raw  # type: ignore[return-value]


def _wires(blueprint: dict[str, object]) -> list[object]:
    raw = blueprint.setdefault("wires", [])
    if not isinstance(raw, list):
        raise ValueError("blueprint wires must be a list")
    return raw


def _dock_id(entities: Iterable[dict[str, object]], label: str) -> int:
    expected = f"DOCK {label}"
    matches = [
        int(entity["entity_number"])
        for entity in entities
        if entity.get("player_description") == expected
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {expected!r}, got {len(matches)}")
    return matches[0]


def _wire(
    wires: list[object],
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> None:
    wires.append(list(_normalized_wire(left, left_connector, right, right_connector)))


def _normalized_wire(
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> tuple[int, int, int, int]:
    if left > right:
        return (right, right_connector, left, left_connector)
    return (left, left_connector, right, right_connector)


def _wire_tuple(raw: object) -> tuple[int, int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError(f"invalid blueprint wire {raw!r}")
    return tuple(int(value) for value in raw)  # type: ignore[return-value]


def _validate_wire_references(blueprint: dict[str, object]) -> None:
    ids = {int(entity["entity_number"]) for entity in _entities(blueprint)}
    for raw in _wires(blueprint):
        left, _left_connector, right, _right_connector = _wire_tuple(raw)
        if left not in ids or right not in ids:
            raise ValueError(f"wire {raw!r} references a missing entity")


def _validate_bounds(blueprint: dict[str, object]) -> None:
    for entity in _entities(blueprint):
        position = entity.get("position")
        assert isinstance(position, dict)
        x = float(position["x"])
        y = float(position["y"])
        if not 0.0 <= x <= TILE_WIDTH or not 0.0 <= y <= COMPLETE_TILE_HEIGHT:
            raise ValueError(
                f"complete worker entity {entity['entity_number']} escapes "
                f"{TILE_WIDTH}x{COMPLETE_TILE_HEIGHT} envelope at {(x, y)}"
            )
