"""Independent electrical-connectivity verification for serialized Factorio blueprints.

I2 deliberately consumes only the serialized blueprint artifact plus explicit post-serialization
expectations.  It reuses I1 for local structural validity, then reconstructs red/green connected
components from root-level Factorio wire tuples.  It never consults synthesis ``Layout`` objects,
abstract/concrete net ids, signal allocation, or routed-net grouping state.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from factorio_circuit.blueprint.verify import (
    BlueprintPrototypeSpec,
    BlueprintVerificationError,
    decode_blueprint_string,
    verify_blueprint_structure,
)

_PORT_ANNOTATION = re.compile(
    r"^\[FCC #(?P<entity_id>[1-9][0-9]*) \| marker\] "
    r"(?P<direction>INPUT|OUTPUT) (?P<name>.+?) —"
)


class BlueprintPortDirection(StrEnum):
    """Serialized public-port direction encoded by compiler marker annotations."""

    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True, order=True)
class BlueprintEndpoint:
    """One exact serialized Factorio circuit connector endpoint."""

    entity_id: int
    connector_id: int

    def __post_init__(self) -> None:
        if type(self.entity_id) is not int or self.entity_id <= 0:
            raise ValueError("blueprint endpoint entity id must be a positive integer")
        if type(self.connector_id) is not int or self.connector_id <= 0:
            raise ValueError("blueprint endpoint connector id must be a positive integer")

    @property
    def color(self) -> str:
        """Return the Factorio wire colour implied by the connector id parity."""

        return "red" if self.connector_id % 2 else "green"


@dataclass(frozen=True, slots=True)
class BlueprintNetExpectation:
    """One intended physical electrical component in the serialized artifact.

    Every listed endpoint must be mutually connected. Distinct expectations are required to remain
    electrically separate. Callers should therefore describe already-coalesced *physical* groups,
    not abstract logical nets that synthesis is allowed to share safely.
    """

    name: str
    endpoints: tuple[BlueprintEndpoint, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("expected blueprint net name must be non-empty")
        if not self.endpoints:
            raise ValueError("expected blueprint net must contain at least one endpoint")
        if len(set(self.endpoints)) != len(self.endpoints):
            raise ValueError(f"expected blueprint net {self.name!r} contains duplicate endpoints")
        colors = {endpoint.color for endpoint in self.endpoints}
        if len(colors) != 1:
            raise ValueError(
                f"expected blueprint net {self.name!r} mixes red and green connector ids"
            )


@dataclass(frozen=True, slots=True)
class BlueprintPublicPortExpectation:
    """Expected compiler public marker and optional serialized peers on its electrical net."""

    name: str
    direction: BlueprintPortDirection
    peers: tuple[BlueprintEndpoint, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("expected public port name must be non-empty")
        if not isinstance(self.direction, BlueprintPortDirection):
            raise TypeError("expected public port direction must be BlueprintPortDirection")
        if len(set(self.peers)) != len(self.peers):
            raise ValueError(f"expected public port {self.name!r} contains duplicate peers")


@dataclass(frozen=True, slots=True)
class BlueprintPublicPort:
    """Public compiler marker discovered from the exact serialized blueprint artifact."""

    name: str
    direction: BlueprintPortDirection
    entity_id: int
    connector_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BlueprintConnectivityReport:
    """Success summary for the independently reconstructed serialized circuit graph."""

    network_count: int
    verified_nets: tuple[str, ...]
    public_ports: tuple[BlueprintPublicPort, ...]


@dataclass(frozen=True, slots=True)
class _ConnectivityEntity:
    entity_id: int
    prototype: str
    spec: BlueprintPrototypeSpec
    description: str | None


@dataclass(frozen=True, slots=True)
class _ConnectivityWire:
    left: BlueprintEndpoint
    right: BlueprintEndpoint


def verify_blueprint_connectivity(
    artifact: Mapping[str, object] | str,
    *,
    prototype_specs: Mapping[str, BlueprintPrototypeSpec],
    expected_nets: Sequence[BlueprintNetExpectation] = (),
    expected_public_ports: Sequence[BlueprintPublicPortExpectation] = (),
) -> BlueprintConnectivityReport:
    """Verify serialized electrical equivalence, separation, and public marker contracts.

    I1 structural verification runs first. Red/green components are then reconstructed solely from
    the serialized root-level wire tuples. ``expected_nets`` describes complete intended physical
    equivalence groups among the endpoints that matter to the caller: all endpoints within one
    expectation must be connected, while distinct expectations must remain separate.

    ``expected_public_ports`` is a complete public-marker contract when non-empty. Marker identity
    is reconstructed from the stable serialized ``[FCC #... | marker] INPUT/OUTPUT ...`` annotation.
    Optional peer endpoints must be electrically connected to that marker in the serialized graph.
    """

    root = decode_blueprint_string(artifact) if isinstance(artifact, str) else artifact
    verify_blueprint_structure(root, prototype_specs=prototype_specs)
    blueprint = _blueprint_body(root)
    entities = _parse_entities(blueprint, prototype_specs)
    wires = _parse_wires(blueprint)
    labels = _component_labels(wires)
    ports = _discover_public_ports(entities, wires)

    _verify_net_expectations(expected_nets, entities, labels)
    _verify_public_port_expectations(expected_public_ports, ports, entities, labels)

    return BlueprintConnectivityReport(
        network_count=len(set(labels.values())),
        verified_nets=tuple(expectation.name for expectation in expected_nets),
        public_ports=ports,
    )


def _blueprint_body(root: Mapping[str, object]) -> Mapping[str, object]:
    nested = root.get("blueprint")
    if nested is not None:
        return _require_mapping(nested, "blueprint wrapper member")
    if "entities" in root or root.get("item") == "blueprint":
        return root
    raise BlueprintVerificationError("artifact does not contain an ordinary blueprint")


def _parse_entities(
    blueprint: Mapping[str, object],
    prototype_specs: Mapping[str, BlueprintPrototypeSpec],
) -> dict[int, _ConnectivityEntity]:
    raw_entities = _require_sequence(blueprint.get("entities", ()), "blueprint entities")
    result: dict[int, _ConnectivityEntity] = {}
    for index, raw_entity in enumerate(raw_entities):
        entity = _require_mapping(raw_entity, f"blueprint entity entry {index}")
        entity_id = cast(int, entity["entity_number"])
        prototype = cast(str, entity["name"])
        description = entity.get("player_description")
        result[entity_id] = _ConnectivityEntity(
            entity_id,
            prototype,
            prototype_specs[prototype],
            description if isinstance(description, str) else None,
        )
    return result


def _parse_wires(blueprint: Mapping[str, object]) -> tuple[_ConnectivityWire, ...]:
    raw_wires = _require_sequence(blueprint.get("wires", ()), "blueprint wires")
    result: list[_ConnectivityWire] = []
    for index, raw_wire in enumerate(raw_wires):
        values = _require_sequence(raw_wire, f"blueprint wire entry {index}")
        left_id, left_connector_id, right_id, right_connector_id = cast(
            tuple[int, int, int, int],
            tuple(values),
        )
        result.append(
            _ConnectivityWire(
                BlueprintEndpoint(left_id, left_connector_id),
                BlueprintEndpoint(right_id, right_connector_id),
            )
        )
    return tuple(result)


def _component_labels(wires: Sequence[_ConnectivityWire]) -> dict[BlueprintEndpoint, int]:
    adjacency: dict[BlueprintEndpoint, set[BlueprintEndpoint]] = defaultdict(set)
    for wire in wires:
        adjacency[wire.left].add(wire.right)
        adjacency[wire.right].add(wire.left)

    labels: dict[BlueprintEndpoint, int] = {}
    next_label = 0
    for start in sorted(adjacency):
        if start in labels:
            continue
        stack = [start]
        labels[start] = next_label
        while stack:
            current = stack.pop()
            for neighbor in sorted(adjacency[current], reverse=True):
                if neighbor in labels:
                    continue
                labels[neighbor] = next_label
                stack.append(neighbor)
        next_label += 1
    return labels


def _discover_public_ports(
    entities: Mapping[int, _ConnectivityEntity],
    wires: Sequence[_ConnectivityWire],
) -> tuple[BlueprintPublicPort, ...]:
    used_connectors: dict[int, set[int]] = defaultdict(set)
    for wire in wires:
        used_connectors[wire.left.entity_id].add(wire.left.connector_id)
        used_connectors[wire.right.entity_id].add(wire.right.connector_id)

    ports: dict[tuple[BlueprintPortDirection, str], BlueprintPublicPort] = {}
    for entity_id in sorted(entities):
        entity = entities[entity_id]
        if entity.description is None:
            continue
        match = _PORT_ANNOTATION.match(entity.description)
        if match is None:
            continue
        annotated_id = int(match.group("entity_id"))
        if annotated_id != entity_id:
            raise BlueprintVerificationError(
                f"public marker annotation id {annotated_id} does not match "
                f"entity number {entity_id}"
            )
        if entity.prototype != "constant-combinator":
            raise BlueprintVerificationError(
                f"public marker entity {entity_id} uses prototype {entity.prototype!r}; "
                "compiler public markers must be constant-combinators"
            )

        direction = BlueprintPortDirection(match.group("direction").lower())
        name = match.group("name")
        connector_ids = tuple(sorted(used_connectors.get(entity_id, set())))
        if len(connector_ids) > 1:
            raise BlueprintVerificationError(
                f"public {direction.value} port {name!r} entity {entity_id} is wired to both "
                "red and green networks"
            )
        key = (direction, name)
        if key in ports:
            raise BlueprintVerificationError(
                f"serialized blueprint contains duplicate public {direction.value} port {name!r}"
            )
        ports[key] = BlueprintPublicPort(name, direction, entity_id, connector_ids)

    return tuple(
        sorted(
            ports.values(),
            key=lambda port: (port.direction.value, port.name, port.entity_id),
        )
    )


def _verify_net_expectations(
    expected_nets: Sequence[BlueprintNetExpectation],
    entities: Mapping[int, _ConnectivityEntity],
    labels: Mapping[BlueprintEndpoint, int],
) -> None:
    names: set[str] = set()
    endpoint_owner: dict[BlueprintEndpoint, str] = {}
    for expectation in expected_nets:
        if expectation.name in names:
            raise ValueError(f"duplicate expected blueprint net name {expectation.name!r}")
        names.add(expectation.name)
        for endpoint in expectation.endpoints:
            _validate_expected_endpoint(endpoint, entities)
            previous = endpoint_owner.setdefault(endpoint, expectation.name)
            if previous != expectation.name:
                raise ValueError(
                    f"expected endpoint {endpoint} appears in both {previous!r} and "
                    f"{expectation.name!r}"
                )

        anchor = expectation.endpoints[0]
        for endpoint in expectation.endpoints[1:]:
            if not _connected(anchor, endpoint, labels):
                raise BlueprintVerificationError(
                    f"expected blueprint net {expectation.name!r} is disconnected between "
                    f"{anchor} and {endpoint}"
                )

    expectations = tuple(expected_nets)
    for left_index, left in enumerate(expectations):
        left_endpoint = left.endpoints[0]
        for right in expectations[left_index + 1 :]:
            right_endpoint = right.endpoints[0]
            if _connected(left_endpoint, right_endpoint, labels):
                raise BlueprintVerificationError(
                    f"expected blueprint nets {left.name!r} and {right.name!r} are "
                    "unexpectedly shorted together"
                )


def _verify_public_port_expectations(
    expected_ports: Sequence[BlueprintPublicPortExpectation],
    ports: Sequence[BlueprintPublicPort],
    entities: Mapping[int, _ConnectivityEntity],
    labels: Mapping[BlueprintEndpoint, int],
) -> None:
    if not expected_ports:
        return

    expected: dict[tuple[BlueprintPortDirection, str], BlueprintPublicPortExpectation] = {}
    for item in expected_ports:
        key = (item.direction, item.name)
        if key in expected:
            raise ValueError(f"duplicate expected public {item.direction.value} port {item.name!r}")
        expected[key] = item

    discovered = {(port.direction, port.name): port for port in ports}
    missing = sorted(
        f"{direction.value}:{name}" for direction, name in set(expected) - set(discovered)
    )
    extra = sorted(
        f"{direction.value}:{name}" for direction, name in set(discovered) - set(expected)
    )
    if missing or extra:
        raise BlueprintVerificationError(
            f"serialized public-port contract mismatch; missing={missing}, extra={extra}"
        )

    for key, expectation in expected.items():
        port = discovered[key]
        if not expectation.peers:
            continue
        if len(port.connector_ids) != 1:
            raise BlueprintVerificationError(
                f"public {port.direction.value} port {port.name!r} is not connected to exactly "
                "one serialized wire colour"
            )
        marker = BlueprintEndpoint(port.entity_id, port.connector_ids[0])
        for peer in expectation.peers:
            _validate_expected_endpoint(peer, entities)
            if peer.color != marker.color:
                raise BlueprintVerificationError(
                    f"public {port.direction.value} port {port.name!r} uses {marker.color} but "
                    f"expected peer {peer} uses {peer.color}"
                )
            if not _connected(marker, peer, labels):
                raise BlueprintVerificationError(
                    f"public {port.direction.value} port {port.name!r} is disconnected from "
                    f"expected peer {peer}"
                )


def _validate_expected_endpoint(
    endpoint: BlueprintEndpoint,
    entities: Mapping[int, _ConnectivityEntity],
) -> None:
    try:
        entity = entities[endpoint.entity_id]
    except KeyError as exc:
        raise BlueprintVerificationError(
            f"expected blueprint endpoint refers to absent entity {endpoint.entity_id}"
        ) from exc
    if endpoint.connector_id not in entity.spec.connector_ids:
        raise BlueprintVerificationError(
            f"expected blueprint endpoint {endpoint} is not exposed by prototype "
            f"{entity.prototype!r}"
        )


def _connected(
    left: BlueprintEndpoint,
    right: BlueprintEndpoint,
    labels: Mapping[BlueprintEndpoint, int],
) -> bool:
    if left == right:
        return True
    left_label = labels.get(left)
    return left_label is not None and left_label == labels.get(right)


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise BlueprintVerificationError(f"{context} must be a string-keyed mapping")
    return cast(Mapping[str, object], value)


def _require_sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise BlueprintVerificationError(f"{context} must be a sequence")
    return cast(Sequence[object], value)


__all__ = [
    "BlueprintConnectivityReport",
    "BlueprintEndpoint",
    "BlueprintNetExpectation",
    "BlueprintPortDirection",
    "BlueprintPublicPort",
    "BlueprintPublicPortExpectation",
    "verify_blueprint_connectivity",
]
