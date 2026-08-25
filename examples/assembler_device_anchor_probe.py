"""Probe exact-overlap anchors around AssemblerDevice without any hand-wired component join.

The command component publishes an iron-gear recipe and a two-iron-plate requester setpoint. The
observer component consumes the device's ``ingredients`` output and lights a lamp when iron plates
are present. Both are attached to AssemblerDevice by merging named anchor terminals; the composer
adds no circuit wire between independently generated components.
"""

from __future__ import annotations

from factorio_circuit.devices import (
    AnchorBinding,
    AnchoredBlueprint,
    AnchorSpec,
    AssemblerDevice,
    BoundAnchor,
    compose_anchored_blueprints,
    device_as_anchored_blueprint,
)
from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

IRON_GEAR = SignalId("item", "iron-gear-wheel")
IRON_PLATE = SignalId("item", "iron-plate")
FACTORIO_BLUEPRINT_VERSION = 562954249306113


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _constant_behavior(signal: SignalId, count: int) -> dict[str, object]:
    return {
        "sections": {
            "sections": [
                {
                    "index": 1,
                    "filters": [
                        {
                            "index": 1,
                            "type": signal.kind,
                            "name": signal.name,
                            "quality": "normal",
                            "comparator": "=",
                            "count": count,
                        }
                    ],
                }
            ]
        }
    }


def _output_anchor(
    entity_number: int,
    name: str,
    position: tuple[float, float],
    signal: SignalId,
    count: int,
) -> tuple[dict[str, object], BoundAnchor]:
    entity = {
        "entity_number": entity_number,
        "name": "constant-combinator",
        "position": {"x": position[0], "y": position[1]},
        "player_description": f"ANCHOR PROBE OUTPUT {name}",
        "control_behavior": _constant_behavior(signal, count),
    }
    anchor = BoundAnchor(
        AnchorSpec(
            name,
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.GREEN,
        ),
        entity_number,
        2,
        position,
    )
    return entity, anchor


def _command_component(device: AnchoredBlueprint) -> AnchoredBlueprint:
    recipe = device.anchor("recipe")
    demand = device.anchor("requester_demand")
    recipe_entity, recipe_anchor = _output_anchor(1, "recipe_out", recipe.position, IRON_GEAR, 1)
    demand_entity, demand_anchor = _output_anchor(
        2, "requester_demand_out", demand.position, IRON_PLATE, 2
    )
    blueprint: Blueprint = {
        "item": "blueprint",
        "label": "ANCHOR PROBE command source",
        "version": FACTORIO_BLUEPRINT_VERSION,
        "entities": [recipe_entity, demand_entity],
        "wires": [],
    }
    return AnchoredBlueprint(
        blueprint,
        (recipe_anchor, demand_anchor),
        "anchor-probe-command",
    )


def _observer_component(device: AnchoredBlueprint) -> AnchoredBlueprint:
    ingredients = device.anchor("ingredients")
    anchor_id = 1
    lamp_id = 2
    anchor_entity = {
        "entity_number": anchor_id,
        "name": "constant-combinator",
        "position": {"x": ingredients.position[0], "y": ingredients.position[1]},
        "player_description": "ANCHOR PROBE INPUT ingredients",
    }
    lamp = {
        "entity_number": lamp_id,
        "name": "small-lamp",
        "position": {"x": ingredients.position[0] + 2.5, "y": ingredients.position[1]},
        "player_description": "ANCHOR PROBE ingredients contains iron plate",
        "control_behavior": {
            "circuit_enabled": True,
            "circuit_condition": {
                "first_signal": _signal_json(IRON_PLATE),
                "constant": 0,
                "comparator": ">",
            },
        },
    }
    blueprint: Blueprint = {
        "item": "blueprint",
        "label": "ANCHOR PROBE ingredients observer",
        "version": FACTORIO_BLUEPRINT_VERSION,
        "entities": [anchor_entity, lamp],
        "wires": [[anchor_id, 1, lamp_id, 1]],
    }
    return AnchoredBlueprint(
        blueprint,
        (
            BoundAnchor(
                AnchorSpec(
                    "ingredients_in",
                    DevicePortDirection.INPUT,
                    PayloadShape.VECTOR,
                    TemporalModality.LEVEL,
                    WireColor.RED,
                ),
                anchor_id,
                1,
                ingredients.position,
            ),
        ),
        "anchor-probe-observer",
    )


def build_assembler_device_anchor_probe() -> Blueprint:
    device = device_as_anchored_blueprint(
        AssemblerDevice(label="AssemblerDevice anchored probe").build(),
        label="assembler-device",
    )
    commands = _command_component(device)
    first = compose_anchored_blueprints(
        device,
        commands,
        bindings=(
            AnchorBinding("recipe", "recipe_out"),
            AnchorBinding("requester_demand", "requester_demand_out"),
        ),
        label="AssemblerDevice anchor probe — commands merged",
    )
    intermediate = AnchoredBlueprint(first.blueprint, first.anchors, "device+commands")
    observer = _observer_component(device)
    final = compose_anchored_blueprints(
        intermediate,
        observer,
        bindings=(AnchorBinding("ingredients", "ingredients_in"),),
        label="AssemblerDevice anchor probe — exact-overlap ABI",
    )
    blueprint = final.blueprint
    blueprint["icons"] = [{"signal": {"name": "assembling-machine-3"}, "index": 1}]
    return blueprint


def generate_assembler_device_anchor_probe_string() -> str:
    return encode_blueprint(build_assembler_device_anchor_probe())


def main() -> None:
    print(generate_assembler_device_anchor_probe_string())


if __name__ == "__main__":
    main()
