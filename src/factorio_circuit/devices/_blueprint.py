"""Small codec shared by standalone external-device blueprint generators."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

type Blueprint = dict[str, Any]


def encode_blueprint(blueprint: Blueprint) -> str:
    """Encode one Factorio blueprint object as an import string."""
    payload = json.dumps({"blueprint": blueprint}, separators=(",", ":")).encode()
    return "0" + base64.b64encode(zlib.compress(payload, level=9)).decode("ascii")


def decode_blueprint(blueprint_string: str) -> Blueprint:
    """Decode a Factorio blueprint import string produced by this package."""
    if not blueprint_string.startswith("0"):
        raise ValueError("expected Factorio blueprint string prefix '0'")
    payload = zlib.decompress(base64.b64decode(blueprint_string[1:]))
    decoded = json.loads(payload)
    blueprint = decoded.get("blueprint")
    if not isinstance(blueprint, dict):
        raise ValueError("blueprint string does not contain one blueprint object")
    return blueprint
