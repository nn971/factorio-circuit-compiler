"""Generate complete tileable autonomous-mall worker cells and the standard five-worker row."""

from __future__ import annotations

from examples.autonomous_mall.device_tiles import build_complete_book
from examples.autonomous_mall.manual_controller import _compose_controller, compile_manual_tiles

_PROTOTYPE_NAME_FIXES = {
    "logistic-chest-requester": "requester-chest",
    "logistic-chest-passive-provider": "passive-provider-chest",
}


def _normalize_factorio_prototype_names(payload: dict[str, object]) -> dict[str, object]:
    """Replace generator-facing aliases with actual Factorio entity prototype names."""

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
        if not isinstance(blueprint, dict):
            continue
        entities = blueprint.get("entities", [])
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            name = entity.get("name")
            if isinstance(name, str) and name in _PROTOTYPE_NAME_FIXES:
                entity["name"] = _PROTOTYPE_NAME_FIXES[name]
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
    return _normalize_factorio_prototype_names(payload)


def main() -> None:
    from factorio_circuit.synthesis.interface import encode_blueprint_payload

    print(encode_blueprint_payload(build_complete_blueprint_book()))


if __name__ == "__main__":
    main()
