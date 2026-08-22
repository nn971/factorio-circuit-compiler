"""Adapt compiler-generated module ports to the exact-overlap external-device anchor ABI.

This module is the only sanctioned post-compilation electrical adaptation between a compiled module
and an independently generated external device.  It operates exclusively on named compiler I/O
ports:
it never searches descriptions or reaches into component internals.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import ceil, hypot
from typing import Sequence

from factorio_circuit.compiler import CompilationResult
from factorio_circuit.devices.anchors import AnchorSpec, AnchoredBlueprint, BoundAnchor
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import InputPort, OutputPort, SignalId, WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

_EACH = SignalId("virtual", "signal-each")
_DEFAULT_HOP = 7.5


@dataclass(frozen=True, slots=True)
class CompiledAnchorBinding:
    """Bind one named compiler port to one typed physical anchor position."""

    port: str
    spec: AnchorSpec
    position: tuple[float, float]
    route: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.port:
            raise ValueError("compiled anchor port name must be non-empty")
        if self.spec.modality is not TemporalModality.LEVEL:
            raise ValueError(
                f"compiled anchor {self.spec.name!r} currently supports Level ports only"
            )


def compiled_module_as_anchored_blueprint(
    result: CompilationResult,
    bindings: Sequence[CompiledAnchorBinding],
    *,
    label: str | None = None,
    max_hop: float = _DEFAULT_HOP,
) -> AnchoredBlueprint:
    """Materialize stable colored/fixed-signal anchors around a compiled module.

    Compiler-owned marker entities keep their synthesized signal/color.  For each public binding we
    insert one arithmetic isolation/renaming adapter, then route only the *external* side through
    constant-combinator relays to the requested anchor.  Therefore arbitrary compiler allocation
    choices end at this boundary while all cross-component composition remains exact-overlap only.
    """

    if max_hop <= 0:
        raise ValueError("max_hop must be positive")
    if not bindings:
        raise ValueError("compiled anchored module requires at least one binding")
    ports = [binding.port for binding in bindings]
    names = [binding.spec.name for binding in bindings]
    positions = [binding.position for binding in bindings]
    if len(set(ports)) != len(ports):
        raise ValueError("compiled module port cannot be anchored more than once")
    if len(set(names)) != len(names):
        raise ValueError("compiled anchor names must be unique")
    if len(set(positions)) != len(positions):
        raise ValueError("compiled anchors must occupy distinct positions")

    wrapper = deepcopy(result.blueprint_json)
    blueprint = wrapper.get("blueprint")
    if not isinstance(blueprint, dict):
        raise ValueError("compiled result does not contain a blueprint")
    raw_entities = blueprint.setdefault("entities", [])
    raw_wires = blueprint.setdefault("wires", [])
    if not isinstance(raw_entities, list) or not all(
        isinstance(item, dict) for item in raw_entities
    ):
        raise ValueError("compiled blueprint entities must be dictionaries")
    if not isinstance(raw_wires, list):
        raise ValueError("compiled blueprint wires must be a list")
    entities: list[dict[str, object]] = raw_entities  # type: ignore[assignment]
    wires: list[object] = raw_wires

    next_entity = max((int(entity["entity_number"]) for entity in entities), default=0) + 1
    anchors: list[BoundAnchor] = []
    for binding in bindings:
        port = _resolve_port(result, binding)
        _validate_shape_and_direction(port, binding.spec)
        internal_color = _port_color(result, port.marker_entity)
        internal_position = _entity_position(entities, port.marker_entity)

        adapter_position = _adapter_position(internal_position, binding.position)
        adapter_id = next_entity
        next_entity += 1
        anchor_id = next_entity
        next_entity += 1

        adapter = _adapter_entity(
            adapter_id,
            adapter_position,
            port=port,
            spec=binding.spec,
            internal_color=internal_color,
        )
        anchor = {
            "entity_number": anchor_id,
            "name": "constant-combinator",
            "position": {"x": binding.position[0], "y": binding.position[1]},
            "player_description": (
                f"ANCHOR {binding.spec.name} — {binding.spec.direction.value} "
                f"{binding.spec.payload_shape.value} {binding.spec.modality.value}; "
                f"{binding.spec.wire.value.upper()}"
            ),
        }
        entities.extend((adapter, anchor))

        if binding.spec.direction is DevicePortDirection.INPUT:
            _append_wire(
                wires,
                adapter_id,
                _arithmetic_output_connector(internal_color),
                port.marker_entity,
                _constant_connector(internal_color),
            )
            first_entity = adapter_id
            first_connector = _arithmetic_input_connector(binding.spec.wire)
        else:
            _append_wire(
                wires,
                port.marker_entity,
                _constant_connector(internal_color),
                adapter_id,
                _arithmetic_input_connector(internal_color),
            )
            first_entity = adapter_id
            first_connector = _arithmetic_output_connector(binding.spec.wire)

        next_entity = _route_external_side(
            entities,
            wires,
            first_entity=first_entity,
            first_connector=first_connector,
            first_position=adapter_position,
            anchor_entity=anchor_id,
            anchor_position=binding.position,
            color=binding.spec.wire,
            next_entity=next_entity,
            max_hop=max_hop,
            waypoints=binding.route,
            description=f"ANCHOR RELAY {binding.spec.name}",
        )
        anchors.append(
            BoundAnchor(
                binding.spec,
                anchor_id,
                _constant_connector(binding.spec.wire),
                binding.position,
            )
        )

    blueprint["wires"] = [
        list(wire) for wire in sorted({_wire_tuple(raw) for raw in wires})
    ]
    if label is not None:
        blueprint["label"] = label
    return AnchoredBlueprint(blueprint, tuple(anchors), label or result.layout.name)


def _resolve_port(
    result: CompilationResult, binding: CompiledAnchorBinding
) -> InputPort | OutputPort:
    candidates: Sequence[InputPort | OutputPort]
    if binding.spec.direction is DevicePortDirection.INPUT:
        candidates = result.physical_circuit.inputs
    else:
        candidates = result.physical_circuit.outputs
    matches = [port for port in candidates if port.name == binding.port]
    if len(matches) != 1:
        raise ValueError(
            f"compiled {binding.spec.direction.value} port {binding.port!r} resolved to "
            f"{len(matches)} ports"
        )
    return matches[0]


def _validate_shape_and_direction(
    port: InputPort | OutputPort, spec: AnchorSpec
) -> None:
    shape = PayloadShape.VECTOR if port.signal is None else PayloadShape.SCALAR
    if shape is not spec.payload_shape:
        raise ValueError(
            f"compiled port {port.name!r} is {shape.value}, anchor {spec.name!r} "
            f"requires {spec.payload_shape.value}"
        )
    if shape is PayloadShape.SCALAR and spec.signal is None:
        raise ValueError(f"scalar anchor {spec.name!r} requires a stable external signal")


def _port_color(result: CompilationResult, marker_entity: int) -> WireColor:
    colors = {
        wire.color
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        raise ValueError(
            f"compiled port marker {marker_entity} must have exactly one internal wire color; "
            f"got {sorted(color.value for color in colors)}"
        )
    return next(iter(colors))


def _adapter_position(
    internal: tuple[float, float], external: tuple[float, float]
) -> tuple[float, float]:
    dx = external[0] - internal[0]
    dy = external[1] - internal[1]
    distance = hypot(dx, dy)
    if distance < 1.0:
        raise ValueError(
            f"compiled marker at {internal!r} is too close to external anchor {external!r} "
            "for an isolation adapter"
        )
    step = min(3.5, distance / 2.0)
    return (internal[0] + dx * step / distance, internal[1] + dy * step / distance)


def _adapter_entity(
    entity_id: int,
    position: tuple[float, float],
    *,
    port: InputPort | OutputPort,
    spec: AnchorSpec,
    internal_color: WireColor,
) -> dict[str, object]:
    vector = port.signal is None
    if spec.direction is DevicePortDirection.INPUT:
        read_color = spec.wire
        first_signal = _EACH if vector else spec.signal
        output_signal = _EACH if vector else port.signal
    else:
        read_color = internal_color
        first_signal = _EACH if vector else port.signal
        output_signal = _EACH if vector else spec.signal
    if first_signal is None or output_signal is None:
        raise ValueError(f"scalar anchor {spec.name!r} has unresolved signal mapping")
    return {
        "entity_number": entity_id,
        "name": "arithmetic-combinator",
        "position": {"x": position[0], "y": position[1]},
        "direction": 4,
        "player_description": f"ANCHOR ADAPTER {spec.name}",
        "control_behavior": {
            "arithmetic_conditions": {
                "operation": "*",
                "first_signal": _signal_json(first_signal),
                "first_signal_networks": _network_selection(read_color),
                "second_constant": 1,
                "output_signal": _signal_json(output_signal),
            }
        },
    }


def _route_external_side(
    entities: list[dict[str, object]],
    wires: list[object],
    *,
    first_entity: int,
    first_connector: int,
    first_position: tuple[float, float],
    anchor_entity: int,
    anchor_position: tuple[float, float],
    color: WireColor,
    next_entity: int,
    max_hop: float,
    waypoints: Sequence[tuple[float, float]],
    description: str,
) -> int:
    previous_entity = first_entity
    previous_connector = first_connector
    previous_position = first_position
    relay_connector = _constant_connector(color)
    targets = (*waypoints, anchor_position)
    relay_index = 0
    for target_index, target in enumerate(targets):
        distance = hypot(target[0] - previous_position[0], target[1] - previous_position[1])
        if distance == 0:
            continue
        segments = max(1, ceil(distance / max_hop))
        segment_start = previous_position
        for step in range(1, segments + 1):
            is_anchor = target_index == len(targets) - 1 and step == segments
            if is_anchor:
                _append_wire(
                    wires,
                    previous_entity,
                    previous_connector,
                    anchor_entity,
                    relay_connector,
                )
                previous_position = anchor_position
                break
            t = step / segments
            position = (
                segment_start[0] + (target[0] - segment_start[0]) * t,
                segment_start[1] + (target[1] - segment_start[1]) * t,
            )
            relay_id = next_entity
            next_entity += 1
            relay_index += 1
            entities.append(
                {
                    "entity_number": relay_id,
                    "name": "constant-combinator",
                    "position": {"x": position[0], "y": position[1]},
                    "player_description": f"{description} {relay_index}",
                }
            )
            _append_wire(
                wires, previous_entity, previous_connector, relay_id, relay_connector
            )
            previous_entity = relay_id
            previous_connector = relay_connector
            previous_position = position
    return next_entity


def _entity_position(
    entities: Sequence[dict[str, object]], entity_id: int
) -> tuple[float, float]:
    matches = [entity for entity in entities if int(entity["entity_number"]) == entity_id]
    if len(matches) != 1:
        raise ValueError(f"compiled marker entity {entity_id} resolved to {len(matches)} entities")
    position = matches[0].get("position")
    if not isinstance(position, dict):
        raise ValueError(f"compiled marker entity {entity_id} has no position")
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
        return right, right_connector, left, left_connector
    return left, left_connector, right, right_connector


def _wire_tuple(raw: object) -> tuple[int, int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError(f"invalid blueprint wire {raw!r}")
    return tuple(int(value) for value in raw)  # type: ignore[return-value]
