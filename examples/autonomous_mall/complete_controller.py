"""Generate complete tileable autonomous-mall worker cells and the standard five-worker row."""

from __future__ import annotations

from math import hypot

from examples.autonomous_mall.device_tiles import build_complete_book
from examples.autonomous_mall.manual_controller import _compose_controller, compile_manual_tiles

_PROTOTYPE_NAME_FIXES = {
    "logistic-chest-requester": "requester-chest",
    "logistic-chest-passive-provider": "passive-provider-chest",
    "stack-inserter": "bulk-inserter",
}


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
    return min(matches, key=lambda entity: hypot(_position(entity)[0] - mx, _position(entity)[1] - my))


def _normalize_worker_devices(blueprint: dict[str, object]) -> None:
    """Apply Factorio prototype names and footprint-aware machine-bay geometry."""

    raw_entities = blueprint.get("entities", [])
    if not isinstance(raw_entities, list):
        raise ValueError("blueprint entities must be a list")
    entities = [entity for entity in raw_entities if isinstance(entity, dict)]

    for entity in entities:
        name = entity.get("name")
        if isinstance(name, str) and name in _PROTOTYPE_NAME_FIXES:
            entity["name"] = _PROTOTYPE_NAME_FIXES[name]

    # The assembler bay is horizontal: requester -> feeder -> 3x3 assembler -> inserter -> provider.
    # Factorio 2.x uses 16 direction values, so east is 4. Assembling-machine-3 itself has no
    # orientation field.
    for machine in [entity for entity in entities if entity.get("name") == "assembling-machine-3"]:
        feeder = _nearest_device_entity(entities, machine, "MALL DEVICE feeder")
        output_inserter = _nearest_device_entity(entities, machine, "MALL DEVICE output inserter")
        feeder["direction"] = 4
        output_inserter["direction"] = 4
        machine.pop("direction", None)

    # Recycler is a different physical machine. Its native north-facing footprint is 2x4 and the
    # prototype output vector is (-0.35, -2.3), so the output lands in the chest centered 0.5 tiles
    # west and 2.5 tiles north of the recycler center. Feed from the south; north is direction 0 in
    # Factorio 2.x. No output inserter is used.
    recycler_output_inserters: set[int] = set()
    for machine in [entity for entity in entities if entity.get("name") == "recycler"]:
        old_x, _old_y = _position(machine)
        tile_origin = round((old_x - 17.5) / 48.0) * 48.0
        center_x = tile_origin + 17.0
        center_y = 59.0

        requester = _nearest_device_entity(entities, machine, "MALL DEVICE requester")
        feeder = _nearest_device_entity(entities, machine, "MALL DEVICE feeder")
        provider = _nearest_device_entity(entities, machine, "MALL DEVICE output provider")
        output_inserter = _nearest_device_entity(entities, machine, "MALL DEVICE output inserter")

        _set_position(machine, center_x, center_y)
        machine["direction"] = 0
        _set_position(feeder, center_x - 0.5, center_y + 2.5)
        feeder["direction"] = 0
        _set_position(requester, center_x - 0.5, center_y + 3.5)
        _set_position(provider, center_x - 0.5, center_y - 2.5)
        recycler_output_inserters.add(int(output_inserter["entity_number"]))

    if recycler_output_inserters:
        blueprint["entities"] = [
            entity
            for entity in raw_entities
            if not (
                isinstance(entity, dict)
                and int(entity.get("entity_number", -1)) in recycler_output_inserters
            )
        ]
        raw_wires = blueprint.get("wires", [])
        if isinstance(raw_wires, list):
            blueprint["wires"] = [
                wire
                for wire in raw_wires
                if not (
                    isinstance(wire, list)
                    and len(wire) == 4
                    and (
                        int(wire[0]) in recycler_output_inserters
                        or int(wire[2]) in recycler_output_inserters
                    )
                )
            ]


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
    """Compile controllers once, attach physical devices, and return the complete blueprint book."""

    head, assembler, recycler = compile_manual_tiles()
    controller_only = _compose_controller(head, assembler, recycler)
    payload = build_complete_book(
        head.blueprint,
        assembler.blueprint,
        recycler.blueprint,
        controller_only,
    ).payload()
    return _normalize_complete_book(payload)


def main() -> None:
    from factorio_circuit.synthesis.interface import encode_blueprint_payload

    print(encode_blueprint_payload(build_complete_blueprint_book()))


if __name__ == "__main__":
    main()