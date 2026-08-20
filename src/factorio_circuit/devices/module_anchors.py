"""Bind compiler module ports to exact-overlap external-device anchors.

The compiler is free to choose an internal signal identity and red/green network for a module port.
External devices, by contrast, expose a stable physical ABI.  This adapter inserts exactly one
isolation/renaming arithmetic combinator between each compiled module marker and a named constant-
combinator anchor.  Components are still joined only by :mod:`factorio_circuit.devices.anchors`;
this module never wires into another component's internals.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import hypot
from typing import Sequence

from factorio_circuit.compiler import CompilationResult
from factorio_circuit.devices.anchors import AnchorSpec, AnchoredBlueprint, BoundAnchor
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import InputPort, OutputPort, SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape

Position = tuple[float, float]
_EACH = SignalId("virtual", "signal-each")


@dataclass(frozen=True, slots=True)
class ModuleAnchorBinding:
    """Expose one compiled module I/O port as one typed exact-overlap anchor."""

    port: str
    anchor: AnchorSpec
    position: Position
    adapter_position: Position | None = None

    def __post_init__(self) -> None:
        if not self.port:
            raise ValueError("module anchor binding port must be non-empty")


def compiled_module_as_anchored_blueprint(
    result: CompilationResult,
    bindings: Sequence[ModuleAnchorBinding],
    *,
    label: str | None = None,
    max_wire_span: float = 9.0,
) -> AnchoredBlueprint:
    """Materialize stable device-facing anchors for selected compiled module ports.

    ``AnchorSpec.direction`` is interpreted from the compiled module's point of view: INPUT anchors
    consume a device observation and OUTPUT anchors drive a device command.  A small arithmetic
    adapter hides whatever signal identity/wire color physical synthesis chose internally.
    """

    if max_wire_span <= 0:
        raise ValueError("max_wire_span must be positive")
    if not bindings:
        raise ValueError("compiled module anchoring requires at least one binding")
    ports = [binding.port for binding in bindings]
    names = [binding.anchor.name for binding in bindings]
    if len(set(ports)) != len(ports):
        raise ValueError("one compiled module port cannot be anchored more than once")
    if len(set(names)) != len(names):
        raise ValueError("compiled module anchor names must be unique")

    wrapper = deepcopy(result.blueprint_json)
    blueprint = wrapper.get("blueprint")
    if not isinstance(blueprint, dict):
        raise ValueError("compilation result does not contain one blueprint")
    entities = _entities(blueprint)
    wires = _wires(blueprint)
    next_id = max((int(entity["entity_number"]) for entity in entities), default=0) + 1
    anchors: list[BoundAnchor] = []

    for binding in bindings:
        port = _port(result, binding)
        _validate_shape(port, binding.anchor)
        internal_color = _port_color(result, port.marker_entity)
        internal_position = _entity_position(entities, port.marker_entity)
        adapter_position = binding.adapter_position or (
            (internal_position[0] + binding.position[0]) / 2.0,
            (internal_position[1] + binding.position[1]) / 2.0,
        )
        _validate_span(internal_position, adapter_position, max_wire_span, binding.port)
        _validate_span(adapter_position, binding.position, max_wire_span, binding.port)

        adapter_id = next_id
        anchor_id = next_id + 1
        next_id += 2
        vector = port.signal is None

        if binding.anchor.direction is DevicePortDirection.INPUT:
            first_signal = binding.anchor.signal
            output_signal = port.signal
            read_color = binding.anchor.wire
        else:
            first_signal = port.signal
            output_signal = binding.anchor.signal
            read_color = internal_color

        conditions: dict[str, object] = {
            "operation": "*",
            "second_constant": 1,
            "first_signal_networks": _network_selection(read_color),
        }
        if vector:
            conditions["first_signal"] = _signal_json(_EACH)
            conditions["output_signal"] = _signal_json(_EACH)
        else:
            assert first_signal is not None
            assert output_signal is not None
            conditions["first_signal"] = _signal_json(first_signal)
            conditions["output_signal"] = _signal_json(output_signal)

        entities.extend(
            [
                {
                    "entity_number": adapter_id,
                    "name": "arithmetic-combinator",
                    "position": {"x": adapter_position[0], "y": adapter_position[1]},
                    "direction": 4,
                    "player_description": (
                        f"MODULE ANCHOR {binding.anchor.direction.value.upper()} "
                        f"{binding.anchor.name} — internal={internal_color.value}; "
                        f"external={binding.anchor.wire.value}"
                    ),
                    "control_behavior": {"arithmetic_conditions": conditions},
                },
                {
                    "entity_number": anchor_id,
                    "name": "constant-combinator",
                    "position": {"x": binding.position[0], "y": binding.position[1]},
                    "player_description": f"MODULE ANCHOR {binding.anchor.name}",
                },
            ]
        )

        internal_connector = _constant_connector(internal_color)
        external_connector = _constant_connector(binding.anchor.wire)
        if binding.anchor.direction is DevicePortDirection.INPUT:
            _append_wire(
                wires,
                anchor_id,
                external_connector,
                adapter_id,
                _arithmetic_input_connector(binding.anchor.wire),
            )
            _append_wire(
                wires,
                adapter_id,
                _arithmetic_output_connector(internal_color),
                port.marker_entity,
                internal_connector,
            )
        else:
            _append_wire(
                wires,
                port.marker_entity,
                internal_connector,
                adapter_id,
                _arithmetic_input_connector(internal_color),
            )
            _append_wire(
                wires,
                adapter_id,
                _arithmetic_output_connector(binding.anchor.wire),
                anchor_id,
                external_connector,
            )

        anchors.append(
            BoundAnchor(
                binding.anchor,
                anchor_id,
                external_connector,
                binding.position,
            )
        )

    if label is not None:
        blueprint["label"] = label
    blueprint["wires"] = [list(wire) for wire in sorted({_wire_tuple(raw) for raw in wires})]
    return AnchoredBlueprint(blueprint, tuple(anchors), label or result.physical_circuit.name)


def _port(result: CompilationResult, binding: ModuleAnchorBinding) -> InputPort | OutputPort:
    ports = (
        result.physical_circuit.inputs
        if binding.anchor.direction is DevicePortDirection.INPUT
        else result.physical_circuit.outputs
    )
    matches = [port for port in ports if port.name == binding.port]
    if len(matches) != 1:
        raise ValueError(
            f"compiled {binding.anchor.direction.value} port {binding.port!r} "
            f"resolved to {len(matches)} physical ports"
        )
    return matches[0]


def _validate_shape(port: InputPort | OutputPort, anchor: AnchorSpec) -> None:
    actual = PayloadShape.VECTOR if port.signal is None else PayloadShape.SCALAR
    if actual is not anchor.payload_shape:
        raise ValueError(
            f"compiled port {port.name!r} is {actual.value}, anchor {anchor.name!r} "
            f"requires {anchor.payload_shape.value}"
        )


def _port_color(result: CompilationResult, marker_entity: int) -> WireColor:
    colors = {
        wire.color
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        raise ValueError(
            f"module port marker {marker_entity} must have exactly one internal wire color; "
            f"got {sorted(color.value for color in colors)}"
        )
    return next(iter(colors))


def _validate_span(left: Position, right: Position, maximum: float, port: str) -> None:
    distance = hypot(left[0] - right[0], left[1] - right[1])
    if distance > maximum + 1e-9:
        raise ValueError(
            f"module anchor {port!r} requires {distance:.3f}-tile segment, exceeds {maximum:.3f}"
        )


def _entities(blueprint: dict[str, object]) -> list[dict[str, object]]:
    raw = blueprint.setdefault("entities", [])
    if not isinstance(raw, list) or not all(isinstance(entity, dict) for entity in raw):
        raise ValueError("blueprint entities must be dictionaries")
    return raw  # type: ignore[return-value]


def _wires(blueprint: dict[str, object]) -> list[object]:
    raw = blueprint.setdefault("wires", [])
    if not isinstance(raw, list):
        raise ValueError("blueprint wires must be a list")
    return raw


def _entity_position(entities: Sequence[dict[str, object]], entity_number: int) -> Position:
    matches = [entity for entity in entities if int(entity["entity_number"]) == entity_number]
    if len(matches) != 1:
        raise ValueError(f"expected blueprint entity {entity_number}, found {len(matches)}")
    position = matches[0].get("position")
    if not isinstance(position, dict):
        raise ValueError(f"entity {entity_number} has no position")
    return float(position["x"]), float(position["y"])


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _network_selection(color: WireColor) -> dict[str, bool]:
    return {"red": color is WireColor.RED, "green": color is WireColor.GREEN}


def _constant_connector(color: WireColor) -> int:
    return 1 if color is WireColor.RED else 2


def _arithmetic_input_connector(color: WireColor) -> int:
    return 1 if color is WireColor.RED else 2


def _arithmetic_output_connector(color: WireColor) -> int:
    return 3 if color is WireColor.RED else 4


def _append_wire(
    wires: list[object],
    left_entity: int,
    left_connector: int,
    right_entity: int,
    right_connector: int,
) -> None:
    wires.append(list(_normalized_wire(left_entity, left_connector, right_entity, right_connector)))


def _wire_tuple(raw: object) -> tuple[int, int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError(f"invalid blueprint wire {raw!r}")
    return tuple(int(value) for value in raw)  # type: ignore[return-value]


def _normalized_wire(
    left: int,
    left_connector: int,
    right: int,
    right_connector: int,
) -> tuple[int, int, int, int]:
    if left > right:
        return right, right_connector, left, left_connector
    return left, left_connector, right, right_connector
