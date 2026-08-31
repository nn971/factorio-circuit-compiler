"""Independent structural verification for serialized Factorio blueprints.

This module deliberately does not import synthesis ``Layout`` objects or routing validators.  Its
inputs are the serialized blueprint artifact and an explicit catalogue of prototype geometry and
connector facts, so it can catch mistakes introduced while materializing or serializing a layout.
"""

from __future__ import annotations

import base64
import binascii
import json
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import hypot, isfinite
from typing import cast

_EPSILON = 1e-9
_DEFAULT_COMPILER_WIRE_SPAN = 7.0


class BlueprintVerificationError(ValueError):
    """Raised when a serialized blueprint violates the verifier's structural contract."""


@dataclass(frozen=True, slots=True)
class BlueprintPrototypeSpec:
    """Physical facts independently supplied for one serialized entity prototype."""

    half_extent: tuple[float, float]
    connector_ids: frozenset[int]
    maximum_wire_span: float = _DEFAULT_COMPILER_WIRE_SPAN

    def __post_init__(self) -> None:
        half_x, half_y = self.half_extent
        if not isfinite(half_x) or not isfinite(half_y) or half_x <= 0.0 or half_y <= 0.0:
            raise ValueError("prototype half-extents must be finite and positive")
        if not self.connector_ids or any(
            type(connector_id) is not int or connector_id <= 0
            for connector_id in self.connector_ids
        ):
            raise ValueError("prototype connector ids must be positive integers")
        if not isfinite(self.maximum_wire_span) or self.maximum_wire_span <= 0.0:
            raise ValueError("prototype maximum wire span must be finite and positive")


@dataclass(frozen=True, slots=True)
class BlueprintVerificationReport:
    """Small success summary returned after all structural checks pass."""

    entity_count: int
    wire_count: int
    prototypes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedEntity:
    entity_id: int
    prototype: str
    position: tuple[float, float]
    spec: BlueprintPrototypeSpec


def compiler_prototype_specs() -> dict[str, BlueprintPrototypeSpec]:
    """Return the explicit physical catalogue for entities emitted directly by the compiler."""

    single = BlueprintPrototypeSpec((0.5, 0.5), frozenset({1, 2}))
    input_output = BlueprintPrototypeSpec((1.0, 0.5), frozenset({1, 2, 3, 4}))
    return {
        "constant-combinator": single,
        "arithmetic-combinator": input_output,
        "decider-combinator": input_output,
        "selector-combinator": input_output,
    }


def decode_blueprint_string(blueprint_string: str) -> Mapping[str, object]:
    """Decode one ordinary Factorio blueprint import string into its JSON wrapper."""

    if not blueprint_string.startswith("0"):
        raise BlueprintVerificationError("blueprint string must use Factorio version prefix '0'")
    try:
        compressed = base64.b64decode(blueprint_string[1:], validate=True)
        payload = zlib.decompress(compressed).decode("utf-8")
        decoded = json.loads(payload)
    except (binascii.Error, UnicodeDecodeError, ValueError, zlib.error) as exc:
        raise BlueprintVerificationError("blueprint string is not valid compressed JSON") from exc
    return _require_mapping(decoded, "decoded blueprint root")


def verify_blueprint_structure(
    artifact: Mapping[str, object] | str,
    *,
    prototype_specs: Mapping[str, BlueprintPrototypeSpec],
) -> BlueprintVerificationReport:
    """Verify serialized entity geometry, connector references, wire colours, and wire reach.

    ``artifact`` may be either the outer ``{"blueprint": ...}`` JSON object, the inner blueprint
    object, or an encoded Factorio blueprint string.  No synthesis object is consulted.
    """

    root = decode_blueprint_string(artifact) if isinstance(artifact, str) else artifact
    blueprint = _blueprint_body(root)
    entities = _parse_entities(blueprint, prototype_specs)
    _validate_footprints(entities)
    wire_count = _validate_wires(blueprint, entities)
    return BlueprintVerificationReport(
        entity_count=len(entities),
        wire_count=wire_count,
        prototypes=tuple(sorted({entity.prototype for entity in entities.values()})),
    )


def _blueprint_body(root: Mapping[str, object]) -> Mapping[str, object]:
    if "blueprint_book" in root:
        raise BlueprintVerificationError("blueprint books are outside the I1 structural verifier")
    nested = root.get("blueprint")
    if nested is not None:
        return _require_mapping(nested, "blueprint wrapper member")
    if "entities" in root or root.get("item") == "blueprint":
        return root
    raise BlueprintVerificationError("artifact does not contain an ordinary blueprint")


def _parse_entities(
    blueprint: Mapping[str, object],
    prototype_specs: Mapping[str, BlueprintPrototypeSpec],
) -> dict[int, _VerifiedEntity]:
    raw_entities = _require_sequence(blueprint.get("entities", ()), "blueprint entities")
    entities: dict[int, _VerifiedEntity] = {}
    for index, raw_entity in enumerate(raw_entities):
        entity = _require_mapping(raw_entity, f"blueprint entity entry {index}")
        entity_id = entity.get("entity_number")
        if type(entity_id) is not int or entity_id <= 0:
            raise BlueprintVerificationError(
                "blueprint entities require positive integer entity_number values"
            )
        if entity_id in entities:
            raise BlueprintVerificationError(
                f"blueprint contains duplicate entity number {entity_id}"
            )

        prototype = entity.get("name")
        if not isinstance(prototype, str) or not prototype:
            raise BlueprintVerificationError(f"blueprint entity {entity_id} has no prototype name")
        try:
            spec = prototype_specs[prototype]
        except KeyError as exc:
            raise BlueprintVerificationError(
                f"blueprint prototype {prototype!r} has no explicit verifier specification"
            ) from exc

        raw_position = _require_mapping(
            entity.get("position"),
            f"blueprint entity {entity_id} position",
        )
        x = _finite_number(raw_position.get("x"), f"blueprint entity {entity_id} x position")
        y = _finite_number(raw_position.get("y"), f"blueprint entity {entity_id} y position")
        entities[entity_id] = _VerifiedEntity(entity_id, prototype, (x, y), spec)
    return entities


def _validate_footprints(entities: Mapping[int, _VerifiedEntity]) -> None:
    ids = sorted(entities)
    for index, left_id in enumerate(ids):
        left = entities[left_id]
        for right_id in ids[index + 1 :]:
            right = entities[right_id]
            if _footprints_overlap(left, right):
                raise BlueprintVerificationError(
                    f"blueprint physical entities {left_id} and {right_id} overlap under the "
                    "declared verifier prototype geometry"
                )


def _footprints_overlap(left: _VerifiedEntity, right: _VerifiedEntity) -> bool:
    left_half = left.spec.half_extent
    right_half = right.spec.half_extent
    return (
        abs(left.position[0] - right.position[0]) < left_half[0] + right_half[0] - _EPSILON
        and abs(left.position[1] - right.position[1]) < left_half[1] + right_half[1] - _EPSILON
    )


def _validate_wires(
    blueprint: Mapping[str, object],
    entities: Mapping[int, _VerifiedEntity],
) -> int:
    raw_wires = _require_sequence(blueprint.get("wires", ()), "blueprint wires")
    for index, raw_wire in enumerate(raw_wires):
        values = _require_sequence(raw_wire, f"blueprint wire entry {index}")
        if len(values) != 4 or any(type(value) is not int for value in values):
            raise BlueprintVerificationError("blueprint wire entries must contain four integers")
        left_id, left_connector_id, right_id, right_connector_id = cast(
            tuple[int, int, int, int],
            tuple(values),
        )
        try:
            left = entities[left_id]
            right = entities[right_id]
        except KeyError as exc:
            raise BlueprintVerificationError(
                f"blueprint wire {index} refers to unknown entity number {exc.args[0]}"
            ) from exc

        _validate_connector(left, left_connector_id)
        _validate_connector(right, right_connector_id)
        if left_connector_id % 2 != right_connector_id % 2:
            raise BlueprintVerificationError(
                f"blueprint wire {index} connects red and green connector ids"
            )

        distance = hypot(
            left.position[0] - right.position[0],
            left.position[1] - right.position[1],
        )
        maximum_span = min(left.spec.maximum_wire_span, right.spec.maximum_wire_span)
        if distance > maximum_span + _EPSILON:
            raise BlueprintVerificationError(
                f"blueprint wire {index} ({left_id}->{right_id}) spans {distance:.3f} tiles; "
                f"maximum declared verifier span is {maximum_span:.3f}"
            )
    return len(raw_wires)


def _validate_connector(entity: _VerifiedEntity, connector_id: int) -> None:
    if connector_id not in entity.spec.connector_ids:
        raise BlueprintVerificationError(
            f"blueprint entity {entity.entity_id} prototype {entity.prototype!r} cannot use "
            f"connector id {connector_id}"
        )


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise BlueprintVerificationError(f"{context} must be a string-keyed mapping")
    return cast(Mapping[str, object], value)


def _require_sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise BlueprintVerificationError(f"{context} must be a sequence")
    return cast(Sequence[object], value)


def _finite_number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlueprintVerificationError(f"{context} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise BlueprintVerificationError(f"{context} must be finite")
    return result


__all__ = [
    "BlueprintPrototypeSpec",
    "BlueprintVerificationError",
    "BlueprintVerificationReport",
    "compiler_prototype_specs",
    "decode_blueprint_string",
    "verify_blueprint_structure",
]
