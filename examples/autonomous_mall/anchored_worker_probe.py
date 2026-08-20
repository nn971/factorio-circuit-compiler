"""Standalone in-game probe for the compiled anchored AssemblerWorker."""

from __future__ import annotations

from copy import deepcopy

from factorio_circuit.devices._blueprint import encode_blueprint
from factorio_circuit.ir.physical import SignalId

from examples.autonomous_mall.anchored_worker import (
    DISPATCH,
    LAUNCH,
    build_anchored_worker_device,
    compile_assembler_worker,
    worker_as_anchored_blueprint,
)

IRON_PLATE = SignalId("item", "iron-plate")
IRON_GEAR = SignalId("recipe", "iron-gear-wheel")


def _constant_sections(signals: tuple[tuple[SignalId, int], ...]) -> dict[str, object]:
    return {
        "sections": {
            "sections": [
                {
                    "index": 1,
                    "filters": [
                        {
                            "index": index,
                            "type": signal.kind,
                            "name": signal.name,
                            "quality": "normal",
                            "comparator": "=",
                            "count": count,
                        }
                        for index, (signal, count) in enumerate(signals, start=1)
                    ],
                }
            ]
        }
    }


def _entity_by_id(entities: list[dict[str, object]], entity_id: int) -> dict[str, object]:
    matches = [entity for entity in entities if int(entity["entity_number"]) == entity_id]
    if len(matches) != 1:
        raise ValueError(f"expected entity {entity_id}, found {len(matches)}")
    return matches[0]


def build_probe_blueprint() -> dict[str, object]:
    """Build one productivity worker with fixed stock/recipe and editable D/L controls."""

    result = compile_assembler_worker()
    blueprint = result.blueprint_json["blueprint"]
    assert isinstance(blueprint, dict)
    entities_raw = blueprint.get("entities", [])
    if not isinstance(entities_raw, list) or not all(isinstance(item, dict) for item in entities_raw):
        raise ValueError("worker blueprint entities must be dictionaries")
    entities: list[dict[str, object]] = entities_raw  # type: ignore[assignment]

    inputs = {port.name: port for port in result.physical_circuit.inputs}
    _entity_by_id(entities, inputs["available_in"].marker_entity)["control_behavior"] = (
        _constant_sections(((IRON_PLATE, 100),))
    )
    control = _entity_by_id(entities, inputs["control_in"].marker_entity)
    control["control_behavior"] = _constant_sections(((DISPATCH, 0), (LAUNCH, 0)))
    control["player_description"] = "PROBE CONTROL D/L — EDIT HERE"
    recipe = _entity_by_id(entities, inputs["job_recipe"].marker_entity)
    recipe["control_behavior"] = _constant_sections(((IRON_GEAR, 1),))
    recipe["player_description"] = "PROBE RECIPE iron-gear-wheel — fixed"

    for port in result.physical_circuit.outputs:
        if port.name in {"accepted", "busy", "filling", "running", "waiting", "remaining_out"}:
            marker = _entity_by_id(entities, port.marker_entity)
            marker["player_description"] = f"PROBE DEBUG {port.name}"

    worker = worker_as_anchored_blueprint(result)
    # Reuse the canonical composition builder for the same physical device/module configuration.
    from factorio_circuit.devices import AnchorBinding, AssemblerDevice, compose_anchored_blueprints, socketize_assembler_device
    from examples.autonomous_mall.anchored_worker import DEVICE_OFFSET

    device = socketize_assembler_device(
        AssemblerDevice(
            modules=("productivity-module-3",) * 4,
            label="AssemblerDevice productivity probe",
        ).build()
    ).anchored()
    bound_names = ("recipe", "enable", "requester_demand", "ingredients", "requester_contents", "working")
    composed = compose_anchored_blueprints(
        worker,
        device,
        bindings=tuple(AnchorBinding(name, name) for name in bound_names),
        right_offset=DEVICE_OFFSET,
        label="Anchored AssemblerWorker probe — iron gear",
    )
    final = deepcopy(composed.blueprint)
    final.pop("snap-to-grid", None)
    final.pop("absolute-snapping", None)
    final.pop("position-relative-to-grid", None)
    return final


def generate_probe_blueprint_string() -> str:
    return encode_blueprint(build_probe_blueprint())


def main() -> None:
    print(generate_probe_blueprint_string())


if __name__ == "__main__":
    main()
