"""Small raw Factorio 2.x blueprint helpers for game-mechanics probes."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

FACTORIO_BLUEPRINT_VERSION = 562949955518464

CIRCUIT_RED = 1
CIRCUIT_GREEN = 2
COMBINATOR_OUTPUT_RED = 3
COMBINATOR_OUTPUT_GREEN = 4

Blueprint = dict[str, Any]
Entity = dict[str, Any]


def signal(kind: str, name: str) -> dict[str, str]:
    return {"type": kind, "name": name}


def constant_combinator(
    entity_number: int,
    x: float,
    y: float,
    signals: list[tuple[str, str, int]],
    *,
    description: str,
) -> Entity:
    filters = [
        {
            "index": index,
            "name": name,
            "type": kind,
            "quality": "normal",
            "comparator": "=",
            "count": count,
        }
        for index, (kind, name, count) in enumerate(signals, start=1)
    ]
    result: Entity = {
        "entity_number": entity_number,
        "name": "constant-combinator",
        "position": {"x": x, "y": y},
        "player_description": description,
    }
    if filters:
        result["control_behavior"] = {"sections": {"sections": [{"index": 1, "filters": filters}]}}
    return result


def blueprint(
    label: str,
    description: str,
    entities: list[Entity],
    wires: list[list[int]],
) -> Blueprint:
    return {
        "blueprint": {
            "item": "blueprint",
            "label": label,
            "description": description,
            "version": FACTORIO_BLUEPRINT_VERSION,
            "entities": entities,
            "wires": wires,
        }
    }


def encode_blueprint(payload: Blueprint) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "0" + base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")


def decode_blueprint(encoded: str) -> Blueprint:
    if not encoded.startswith("0"):
        raise ValueError("Factorio blueprint strings must start with version byte '0'")
    raw = zlib.decompress(base64.b64decode(encoded[1:]))
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("decoded blueprint root must be an object")
    return decoded


def print_blueprint(payload: Blueprint) -> None:
    print(encode_blueprint(payload))
