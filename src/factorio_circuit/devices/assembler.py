"""Reusable assembler + logistic I/O external device.

``AssemblerDevice`` deliberately stops below application policy.  It presents one Factorio
assembler together with a requester/input path and a provider/output path as a stable typed device:

GREEN command ports
    ``recipe``             Level vector selecting the assembler recipe;
    ``enable``             Level scalar ``signal-E`` gating crafting and input feeding;
    ``requester_demand``   Level vector used as the requester chest's circuit requests.

RED observation ports
    ``ingredients``        sanitized current-recipe ingredient vector;
    ``requester_contents`` actual requester-chest inventory;
    ``provider_contents``  actual active-provider inventory;
    ``working``            Level scalar ``signal-W``;
    ``finished``           Event scalar ``signal-F``.

The requester setpoint is intentionally steady-state.  The device does not interpret it as a
one-transaction quantity and does not contain mall reservation/accounting policy.  The input bulk
inserter runs while ``enable`` is positive; the output inserter always clears completed products
into the provider chest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from factorio_circuit.devices._blueprint import Blueprint
from factorio_circuit.devices.protocol import (
    BoundDevicePort,
    DeviceAttachment,
    DeviceEndpoint,
    DevicePortDirection,
    DevicePortSpec,
    DeviceProtocol,
    DeviceSide,
    ExternalDeviceBlueprint,
)
from factorio_circuit.ir.physical import SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

FACTORIO_BLUEPRINT_VERSION: Final = 562954249306113

ASSEMBLER_ENABLE_SIGNAL: Final = SignalId("virtual", "signal-E")
ASSEMBLER_WORKING_SIGNAL: Final = SignalId("virtual", "signal-W")
ASSEMBLER_FINISHED_SIGNAL: Final = SignalId("virtual", "signal-F")
_EACH: Final = SignalId("virtual", "signal-each")

RED_CONNECTOR: Final = 1
GREEN_CONNECTOR: Final = 2
RED_OUTPUT_CONNECTOR: Final = 3
GREEN_OUTPUT_CONNECTOR: Final = 4

ASSEMBLER_DEVICE_PROTOCOL: Final = DeviceProtocol(
    "assembler-v3",
    (
        DevicePortSpec(
            "recipe",
            DevicePortDirection.INPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.GREEN,
        ),
        DevicePortSpec(
            "enable",
            DevicePortDirection.INPUT,
            PayloadShape.SCALAR,
            TemporalModality.LEVEL,
            WireColor.GREEN,
            ASSEMBLER_ENABLE_SIGNAL,
        ),
        DevicePortSpec(
            "requester_demand",
            DevicePortDirection.INPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.GREEN,
        ),
        DevicePortSpec(
            "ingredients",
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.RED,
        ),
        DevicePortSpec(
            "requester_contents",
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.RED,
        ),
        DevicePortSpec(
            "provider_contents",
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            TemporalModality.LEVEL,
            WireColor.RED,
        ),
        DevicePortSpec(
            "working",
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            TemporalModality.LEVEL,
            WireColor.RED,
            ASSEMBLER_WORKING_SIGNAL,
        ),
        DevicePortSpec(
            "finished",
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            TemporalModality.EVENT,
            WireColor.RED,
            ASSEMBLER_FINISHED_SIGNAL,
        ),
    ),
)

# Stable v3 physical layout.  Every protocol dock is an exact-overlap anchor terminal.  The generic
# anchoring composer may merge these terminal entities with compatible controller anchors while the
# device internals remain opaque.
_RECIPE_DOCK: Final = (1, (1.5, 5.5))
_RECIPE_ISOLATOR: Final = (2, (4.0, 5.5))
_ENABLE_DOCK: Final = (3, (1.5, 10.5))
_ENABLE_ISOLATOR: Final = (4, (4.0, 10.5))
_MACHINE: Final = (5, (8.5, 8.5))
_RAW_OUTPUT_RELAY: Final = (6, (12.5, 8.5))
_INGREDIENT_COPY: Final = (7, (14.5, 4.5))
_CANCEL_WORKING: Final = (8, (14.5, 6.5))
_CANCEL_FINISHED: Final = (9, (14.5, 8.5))
_INGREDIENT_MERGE: Final = (10, (17.0, 6.5))
_INGREDIENT_DOCK: Final = (11, (20.0, 6.5))
_WORKING_EXTRACTOR: Final = (12, (14.5, 11.5))
_WORKING_DOCK: Final = (13, (18.0, 11.5))
_FINISHED_EXTRACTOR: Final = (14, (14.5, 14.0))
_FINISHED_DOCK: Final = (15, (18.0, 14.0))

_REQUESTER: Final = (16, (8.5, 11.5))
_INPUT_INSERTER: Final = (17, (8.5, 10.5))
_OUTPUT_INSERTER: Final = (18, (10.5, 8.5))
_PROVIDER: Final = (19, (11.5, 8.5))
_REQUESTER_DEMAND_DOCK: Final = (20, (1.5, 15.5))
_REQUESTER_DEMAND_ISOLATOR: Final = (21, (4.0, 15.5))
_REQUESTER_CONTENTS_EXTRACTOR: Final = (22, (8.5, 16.0))
_REQUESTER_CONTENTS_DOCK: Final = (23, (12.0, 17.5))
_PROVIDER_CONTENTS_EXTRACTOR: Final = (24, (16.0, 15.5))
_PROVIDER_CONTENTS_DOCK: Final = (25, (18.0, 17.5))


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _networks(*, red: bool, green: bool) -> dict[str, bool]:
    return {"red": red, "green": green}


def _dock(entity_number: int, position: tuple[float, float], description: str) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "constant-combinator",
        "position": {"x": position[0], "y": position[1]},
        "player_description": description,
    }


def _arithmetic(
    entity_number: int,
    position: tuple[float, float],
    *,
    first: SignalId,
    multiplier: int,
    output: SignalId,
    input_wire: WireColor,
    description: str,
) -> dict[str, object]:
    return {
        "entity_number": entity_number,
        "name": "arithmetic-combinator",
        "position": {"x": position[0], "y": position[1]},
        "direction": 4,
        "player_description": description,
        "control_behavior": {
            "arithmetic_conditions": {
                "operation": "*",
                "first_signal": _signal_json(first),
                "first_signal_networks": _networks(
                    red=input_wire is WireColor.RED,
                    green=input_wire is WireColor.GREEN,
                ),
                "second_constant": multiplier,
                "output_signal": _signal_json(output),
            }
        },
    }


def _wire(
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> list[int]:
    if left > right:
        return [right, right_connector, left, left_connector]
    return [left, left_connector, right, right_connector]


def _assembler_entity(
    prototype: str, direction: int | None, modules: tuple[str, ...]
) -> dict[str, object]:
    entity: dict[str, object] = {
        "entity_number": _MACHINE[0],
        "name": prototype,
        "position": {"x": _MACHINE[1][0], "y": _MACHINE[1][1]},
        "player_description": "ASSEMBLER DEVICE machine",
        "control_behavior": {
            "input_networks": _networks(red=False, green=True),
            "output_networks": _networks(red=True, green=False),
            "set_recipe": True,
            "read_contents": False,
            "read_ingredients": True,
            "read_working": True,
            "working_signal": _signal_json(ASSEMBLER_WORKING_SIGNAL),
            "read_recipe_finished": True,
            "recipe_finished_signal": _signal_json(ASSEMBLER_FINISHED_SIGNAL),
            "circuit_enabled": True,
            "circuit_condition": {
                "first_signal": _signal_json(ASSEMBLER_ENABLE_SIGNAL),
                "first_signal_networks": _networks(red=False, green=True),
                "constant": 0,
                "comparator": ">",
            },
        },
    }
    if direction is not None:
        entity["direction"] = direction
    if modules:
        by_name: dict[str, list[int]] = {}
        for stack, module in enumerate(modules):
            by_name.setdefault(module, []).append(stack)
        entity["items"] = [
            {
                "id": {"name": module, "quality": "normal"},
                "items": {
                    "in_inventory": [
                        {"inventory": 4, "stack": stack, "count": 1}
                        for stack in stacks
                    ]
                },
            }
            for module, stacks in by_name.items()
        ]
    return entity


def _requester_entity() -> dict[str, object]:
    return {
        "entity_number": _REQUESTER[0],
        "name": "requester-chest",
        "position": {"x": _REQUESTER[1][0], "y": _REQUESTER[1][1]},
        "player_description": "ASSEMBLER DEVICE requester chest",
        "control_behavior": {
            "input_networks": _networks(red=False, green=True),
            "output_networks": _networks(red=True, green=False),
            "set_requests": True,
            "read_contents": True,
        },
    }


def _provider_entity() -> dict[str, object]:
    return {
        "entity_number": _PROVIDER[0],
        "name": "active-provider-chest",
        "position": {"x": _PROVIDER[1][0], "y": _PROVIDER[1][1]},
        "player_description": "ASSEMBLER DEVICE provider chest",
        "control_behavior": {
            "input_networks": _networks(red=False, green=False),
            "output_networks": _networks(red=True, green=False),
            "read_contents": True,
        },
    }


def _input_inserter_entity() -> dict[str, object]:
    return {
        "entity_number": _INPUT_INSERTER[0],
        "name": "bulk-inserter",
        "position": {"x": _INPUT_INSERTER[1][0], "y": _INPUT_INSERTER[1][1]},
        # Inserter direction is pickup-facing: south pickup -> north drop into assembler.
        "direction": 8,
        "player_description": "ASSEMBLER DEVICE requester -> assembler feeder",
        "control_behavior": {
            "input_networks": _networks(red=False, green=True),
            "output_networks": _networks(red=False, green=False),
            "circuit_enabled": True,
            "circuit_condition": {
                "first_signal": _signal_json(ASSEMBLER_ENABLE_SIGNAL),
                "first_signal_networks": _networks(red=False, green=True),
                "constant": 0,
                "comparator": ">",
            },
        },
    }


def _output_inserter_entity() -> dict[str, object]:
    return {
        "entity_number": _OUTPUT_INSERTER[0],
        "name": "bulk-inserter",
        "position": {"x": _OUTPUT_INSERTER[1][0], "y": _OUTPUT_INSERTER[1][1]},
        # West pickup -> east drop into provider chest.
        "direction": 12,
        "player_description": "ASSEMBLER DEVICE assembler -> provider output",
    }


@dataclass(frozen=True, slots=True)
class AssemblerDevice:
    """Configuration/factory for one reusable assembler + logistic I/O device."""

    prototype: str = "assembling-machine-3"
    direction: int | None = None
    modules: tuple[str, ...] = ()
    label: str = "AssemblerDevice v3"

    def __post_init__(self) -> None:
        if not self.prototype:
            raise ValueError("assembler prototype must be non-empty")
        if self.direction is not None and self.direction not in {0, 4, 8, 12}:
            raise ValueError("assembler direction must be one of 0, 4, 8, 12, or None")
        if len(self.modules) > 4:
            raise ValueError("assembler device supports at most four modules")
        if any(not module for module in self.modules):
            raise ValueError("assembler module names must be non-empty")
        if not self.label:
            raise ValueError("assembler device label must be non-empty")

    @property
    def protocol(self) -> DeviceProtocol:
        return ASSEMBLER_DEVICE_PROTOCOL

    def build(self) -> ExternalDeviceBlueprint:
        entities: list[dict[str, object]] = [
            _dock(*_RECIPE_DOCK, "ASSEMBLER PORT recipe — INPUT Level vector; GREEN"),
            _arithmetic(
                *_RECIPE_ISOLATOR,
                first=_EACH,
                multiplier=1,
                output=_EACH,
                input_wire=WireColor.GREEN,
                description="ASSEMBLER DEVICE isolate recipe input",
            ),
            _dock(*_ENABLE_DOCK, "ASSEMBLER PORT enable — INPUT signal-E Level; GREEN"),
            _arithmetic(
                *_ENABLE_ISOLATOR,
                first=ASSEMBLER_ENABLE_SIGNAL,
                multiplier=1,
                output=ASSEMBLER_ENABLE_SIGNAL,
                input_wire=WireColor.GREEN,
                description="ASSEMBLER DEVICE isolate enable input",
            ),
            _assembler_entity(self.prototype, self.direction, self.modules),
            _dock(*_RAW_OUTPUT_RELAY, "ASSEMBLER DEVICE raw ingredient/status output relay"),
            _arithmetic(
                *_INGREDIENT_COPY,
                first=_EACH,
                multiplier=1,
                output=_EACH,
                input_wire=WireColor.RED,
                description="ASSEMBLER DEVICE copy ingredients",
            ),
            _arithmetic(
                *_CANCEL_WORKING,
                first=ASSEMBLER_WORKING_SIGNAL,
                multiplier=-1,
                output=ASSEMBLER_WORKING_SIGNAL,
                input_wire=WireColor.RED,
                description="ASSEMBLER DEVICE strip working from ingredients",
            ),
            _arithmetic(
                *_CANCEL_FINISHED,
                first=ASSEMBLER_FINISHED_SIGNAL,
                multiplier=-1,
                output=ASSEMBLER_FINISHED_SIGNAL,
                input_wire=WireColor.RED,
                description="ASSEMBLER DEVICE strip finished from ingredients",
            ),
            _dock(*_INGREDIENT_MERGE, "ASSEMBLER DEVICE clean ingredient merge"),
            _dock(*_INGREDIENT_DOCK, "ASSEMBLER PORT ingredients — OUTPUT Level vector; RED"),
            _arithmetic(
                *_WORKING_EXTRACTOR,
                first=ASSEMBLER_WORKING_SIGNAL,
                multiplier=1,
                output=ASSEMBLER_WORKING_SIGNAL,
                input_wire=WireColor.RED,
                description="ASSEMBLER DEVICE extract working",
            ),
            _dock(*_WORKING_DOCK, "ASSEMBLER PORT working — OUTPUT signal-W Level; RED"),
            _arithmetic(
                *_FINISHED_EXTRACTOR,
                first=ASSEMBLER_FINISHED_SIGNAL,
                multiplier=1,
                output=ASSEMBLER_FINISHED_SIGNAL,
                input_wire=WireColor.RED,
                description="ASSEMBLER DEVICE extract finished event",
            ),
            _dock(*_FINISHED_DOCK, "ASSEMBLER PORT finished — OUTPUT signal-F Event; RED"),
            _requester_entity(),
            _input_inserter_entity(),
            _output_inserter_entity(),
            _provider_entity(),
            _dock(
                *_REQUESTER_DEMAND_DOCK,
                "ASSEMBLER PORT requester_demand — INPUT Level vector; GREEN",
            ),
            _arithmetic(
                *_REQUESTER_DEMAND_ISOLATOR,
                first=_EACH,
                multiplier=1,
                output=_EACH,
                input_wire=WireColor.GREEN,
                description="ASSEMBLER DEVICE isolate requester demand",
            ),
            _arithmetic(
                *_REQUESTER_CONTENTS_EXTRACTOR,
                first=_EACH,
                multiplier=1,
                output=_EACH,
                input_wire=WireColor.RED,
                description="ASSEMBLER DEVICE isolate requester contents",
            ),
            _dock(
                *_REQUESTER_CONTENTS_DOCK,
                "ASSEMBLER PORT requester_contents — OUTPUT Level vector; RED",
            ),
            _arithmetic(
                *_PROVIDER_CONTENTS_EXTRACTOR,
                first=_EACH,
                multiplier=1,
                output=_EACH,
                input_wire=WireColor.RED,
                description="ASSEMBLER DEVICE isolate provider contents",
            ),
            _dock(
                *_PROVIDER_CONTENTS_DOCK,
                "ASSEMBLER PORT provider_contents — OUTPUT Level vector; RED",
            ),
        ]

        wires = [
            # Isolated GREEN commands. Recipe/enable merge only at the machine;
            # enable also gates the feeder.
            _wire(_RECIPE_DOCK[0], GREEN_CONNECTOR, _RECIPE_ISOLATOR[0], GREEN_CONNECTOR),
            _wire(
                _RECIPE_ISOLATOR[0],
                GREEN_OUTPUT_CONNECTOR,
                _MACHINE[0],
                GREEN_CONNECTOR,
            ),
            _wire(_ENABLE_DOCK[0], GREEN_CONNECTOR, _ENABLE_ISOLATOR[0], GREEN_CONNECTOR),
            _wire(
                _ENABLE_ISOLATOR[0],
                GREEN_OUTPUT_CONNECTOR,
                _MACHINE[0],
                GREEN_CONNECTOR,
            ),
            _wire(
                _ENABLE_ISOLATOR[0],
                GREEN_OUTPUT_CONNECTOR,
                _INPUT_INSERTER[0],
                GREEN_CONNECTOR,
            ),
            # Requester setpoint is its own GREEN command network; requester inventory
            # leaves on RED.
            _wire(
                _REQUESTER_DEMAND_DOCK[0],
                GREEN_CONNECTOR,
                _REQUESTER_DEMAND_ISOLATOR[0],
                GREEN_CONNECTOR,
            ),
            _wire(
                _REQUESTER_DEMAND_ISOLATOR[0],
                GREEN_OUTPUT_CONNECTOR,
                _REQUESTER[0],
                GREEN_CONNECTOR,
            ),
            _wire(
                _REQUESTER[0],
                RED_CONNECTOR,
                _REQUESTER_CONTENTS_EXTRACTOR[0],
                RED_CONNECTOR,
            ),
            _wire(
                _REQUESTER_CONTENTS_EXTRACTOR[0],
                RED_OUTPUT_CONNECTOR,
                _REQUESTER_CONTENTS_DOCK[0],
                RED_CONNECTOR,
            ),
            # Provider chest is observation-only.
            _wire(
                _PROVIDER[0],
                RED_CONNECTOR,
                _PROVIDER_CONTENTS_EXTRACTOR[0],
                RED_CONNECTOR,
            ),
            _wire(
                _PROVIDER_CONTENTS_EXTRACTOR[0],
                RED_OUTPUT_CONNECTOR,
                _PROVIDER_CONTENTS_DOCK[0],
                RED_CONNECTOR,
            ),
            # Raw assembler RED output fanout.
            _wire(_MACHINE[0], RED_CONNECTOR, _RAW_OUTPUT_RELAY[0], RED_CONNECTOR),
            _wire(
                _RAW_OUTPUT_RELAY[0],
                RED_CONNECTOR,
                _INGREDIENT_COPY[0],
                RED_CONNECTOR,
            ),
            _wire(
                _RAW_OUTPUT_RELAY[0],
                RED_CONNECTOR,
                _CANCEL_WORKING[0],
                RED_CONNECTOR,
            ),
            _wire(
                _RAW_OUTPUT_RELAY[0],
                RED_CONNECTOR,
                _CANCEL_FINISHED[0],
                RED_CONNECTOR,
            ),
            _wire(
                _RAW_OUTPUT_RELAY[0],
                RED_CONNECTOR,
                _WORKING_EXTRACTOR[0],
                RED_CONNECTOR,
            ),
            _wire(
                _RAW_OUTPUT_RELAY[0],
                RED_CONNECTOR,
                _FINISHED_EXTRACTOR[0],
                RED_CONNECTOR,
            ),
            # Sanitized ingredient vector: raw EACH plus W/F cancellation.
            _wire(
                _INGREDIENT_COPY[0],
                RED_OUTPUT_CONNECTOR,
                _INGREDIENT_MERGE[0],
                RED_CONNECTOR,
            ),
            _wire(
                _CANCEL_WORKING[0],
                RED_OUTPUT_CONNECTOR,
                _INGREDIENT_MERGE[0],
                RED_CONNECTOR,
            ),
            _wire(
                _CANCEL_FINISHED[0],
                RED_OUTPUT_CONNECTOR,
                _INGREDIENT_MERGE[0],
                RED_CONNECTOR,
            ),
            _wire(
                _INGREDIENT_MERGE[0],
                RED_CONNECTOR,
                _INGREDIENT_DOCK[0],
                RED_CONNECTOR,
            ),
            _wire(
                _WORKING_EXTRACTOR[0],
                RED_OUTPUT_CONNECTOR,
                _WORKING_DOCK[0],
                RED_CONNECTOR,
            ),
            _wire(
                _FINISHED_EXTRACTOR[0],
                RED_OUTPUT_CONNECTOR,
                _FINISHED_DOCK[0],
                RED_CONNECTOR,
            ),
        ]

        blueprint: Blueprint = {
            "item": "blueprint",
            "label": f"{self.label} — {self.prototype}",
            "version": FACTORIO_BLUEPRINT_VERSION,
            "icons": [{"signal": {"name": self.prototype}, "index": 1}],
            "entities": entities,
            "wires": wires,
        }

        endpoints = {
            "recipe": DeviceEndpoint(
                _RECIPE_DOCK[0], GREEN_CONNECTOR, WireColor.GREEN, _RECIPE_DOCK[1]
            ),
            "enable": DeviceEndpoint(
                _ENABLE_DOCK[0], GREEN_CONNECTOR, WireColor.GREEN, _ENABLE_DOCK[1]
            ),
            "requester_demand": DeviceEndpoint(
                _REQUESTER_DEMAND_DOCK[0],
                GREEN_CONNECTOR,
                WireColor.GREEN,
                _REQUESTER_DEMAND_DOCK[1],
            ),
            "ingredients": DeviceEndpoint(
                _INGREDIENT_DOCK[0], RED_CONNECTOR, WireColor.RED, _INGREDIENT_DOCK[1]
            ),
            "requester_contents": DeviceEndpoint(
                _REQUESTER_CONTENTS_DOCK[0],
                RED_CONNECTOR,
                WireColor.RED,
                _REQUESTER_CONTENTS_DOCK[1],
            ),
            "provider_contents": DeviceEndpoint(
                _PROVIDER_CONTENTS_DOCK[0],
                RED_CONNECTOR,
                WireColor.RED,
                _PROVIDER_CONTENTS_DOCK[1],
            ),
            "working": DeviceEndpoint(
                _WORKING_DOCK[0], RED_CONNECTOR, WireColor.RED, _WORKING_DOCK[1]
            ),
            "finished": DeviceEndpoint(
                _FINISHED_DOCK[0], RED_CONNECTOR, WireColor.RED, _FINISHED_DOCK[1]
            ),
        }
        ports = tuple(BoundDevicePort(spec, endpoints[spec.name]) for spec in self.protocol.ports)
        attachments = (
            # South/east machine faces are occupied by the built-in logistic path.
            DeviceAttachment("machine_north", DeviceSide.NORTH, (8.5, 6.5)),
            DeviceAttachment("machine_west", DeviceSide.WEST, (6.5, 8.5)),
        )
        return ExternalDeviceBlueprint(self.protocol, blueprint, ports, attachments)

    def blueprint_string(self) -> str:
        return self.build().blueprint_string()


def build_assembler_device_blueprint(
    *,
    prototype: str = "assembling-machine-3",
    direction: int | None = None,
    modules: tuple[str, ...] = (),
    label: str = "AssemblerDevice v3",
) -> Blueprint:
    """Convenience wrapper returning only blueprint JSON."""

    return AssemblerDevice(
        prototype=prototype, direction=direction, modules=modules, label=label
    ).build().blueprint


def generate_assembler_device_blueprint_string(
    *,
    prototype: str = "assembling-machine-3",
    direction: int | None = None,
    modules: tuple[str, ...] = (),
    label: str = "AssemblerDevice v3",
) -> str:
    """Convenience wrapper returning an importable Factorio blueprint string."""

    return AssemblerDevice(
        prototype=prototype, direction=direction, modules=modules, label=label
    ).blueprint_string()
