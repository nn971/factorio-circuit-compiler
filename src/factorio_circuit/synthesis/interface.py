"""Named physical interfaces for reusable grid-snapped circuit modules."""

from __future__ import annotations

import base64
import json
import zlib
from copy import deepcopy
from dataclasses import dataclass, field, replace

from factorio_circuit.compiler import CompilationResult, compile_circuit, lower_to_abstract_physical
from factorio_circuit.frontend.symbolic import Circuit
from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit
from factorio_circuit.ir.semantic import CircuitModule
from factorio_circuit.progress import ProgressCallback
from factorio_circuit.synthesis.placement import PlacementOptions

Position = tuple[float, float]
GridSize = tuple[int, int]
GridOffset = tuple[int, int]


@dataclass(frozen=True, slots=True)
class ModuleInterface:
    """Stable named I/O geometry and optional Factorio blueprint snapping metadata.

    ``inputs`` and ``outputs`` map public semantic port names to exact marker-entity
    coordinates. The compiler still places and routes implementation combinators normally;
    only the public interface is mechanically stable.

    ``grid_size`` enables Factorio snap-to-grid metadata so independently compiled modules
    can be stamped on one shared lattice. ``grid_offset`` is used only for absolute
    snapping.
    """

    inputs: dict[str, Position] = field(default_factory=dict)
    outputs: dict[str, Position] = field(default_factory=dict)
    grid_size: GridSize | None = None
    absolute_snapping: bool = True
    grid_offset: GridOffset = (0, 0)

    def validate(self) -> None:
        positions = [*self.inputs.values(), *self.outputs.values()]
        if len(positions) != len(set(positions)):
            raise ValueError("module interface port anchors must occupy distinct positions")
        for name, position in (*self.inputs.items(), *self.outputs.items()):
            if not name:
                raise ValueError("module interface port names must be nonempty")
            if len(position) != 2:
                raise ValueError(f"module interface port {name!r} must have an (x, y) position")
        if self.grid_size is not None:
            width, height = self.grid_size
            if width <= 0 or height <= 0:
                raise ValueError("module interface grid size must be positive")
        if not self.absolute_snapping and self.grid_offset != (0, 0):
            raise ValueError("relative-snapping module interfaces cannot specify a grid offset")


def resolve_interface_anchors(
    circuit: AbstractPhysicalCircuit,
    interface: ModuleInterface,
) -> dict[int, Position]:
    """Resolve named interface ports to concrete annotation-marker entity ids."""

    interface.validate()
    inputs = {port.name: port.endpoint.entity for port in circuit.inputs}
    outputs = {port.name: port.endpoint.entity for port in circuit.outputs}

    missing_inputs = sorted(set(interface.inputs) - set(inputs))
    missing_outputs = sorted(set(interface.outputs) - set(outputs))
    if missing_inputs or missing_outputs:
        details: list[str] = []
        if missing_inputs:
            details.append(f"unknown inputs={missing_inputs}")
        if missing_outputs:
            details.append(f"unknown outputs={missing_outputs}")
        raise ValueError("module interface references " + ", ".join(details))

    anchors: dict[int, Position] = {}
    for name, position in interface.inputs.items():
        anchors[inputs[name]] = position
    for name, position in interface.outputs.items():
        anchors[outputs[name]] = position
    return anchors


def placement_for_interface(
    source: Circuit | CircuitModule,
    interface: ModuleInterface,
    *,
    optimize: bool = True,
    placement: PlacementOptions | None = None,
) -> PlacementOptions:
    """Return placement options with named module ports resolved to entity anchors.

    Lowering is deterministic, so the marker ids resolved here are the same ids used by the
    immediately following canonical ``compile_circuit`` call.
    """

    lowered = lower_to_abstract_physical(source, optimize=optimize)
    anchors = resolve_interface_anchors(lowered.abstract_physical, interface)
    selected = placement or PlacementOptions()

    merged = dict(selected.anchors)
    for entity_id, position in anchors.items():
        previous = merged.get(entity_id)
        if previous is not None and previous != position:
            raise ValueError(
                f"module interface conflicts with explicit anchor for entity {entity_id}: "
                f"{previous} != {position}"
            )
        merged[entity_id] = position
    return replace(selected, anchors=merged)


def compile_module(
    source: Circuit | CircuitModule,
    interface: ModuleInterface,
    *,
    optimize: bool = True,
    placement: PlacementOptions | None = None,
    progress: ProgressCallback | None = None,
) -> CompilationResult:
    """Compile one mechanically stable module and attach optional snapping metadata."""

    selected = placement_for_interface(
        source,
        interface,
        optimize=optimize,
        placement=placement,
    )
    result = compile_circuit(
        source,
        optimize=optimize,
        placement=selected,
        progress=progress,
    )
    if interface.grid_size is None:
        return result

    blueprint_json = deepcopy(result.blueprint_json)
    blueprint = blueprint_json["blueprint"]
    assert isinstance(blueprint, dict)
    width, height = interface.grid_size
    blueprint["snap-to-grid"] = {"x": width, "y": height}
    blueprint["absolute-snapping"] = interface.absolute_snapping
    if interface.absolute_snapping:
        offset_x, offset_y = interface.grid_offset
        blueprint["position-relative-to-grid"] = {"x": offset_x, "y": offset_y}
    else:
        blueprint.pop("position-relative-to-grid", None)

    return replace(
        result,
        blueprint_json=blueprint_json,
        blueprint_string=encode_blueprint_payload(blueprint_json),
    )


def encode_blueprint_payload(payload: dict[str, object]) -> str:
    """Encode an already-materialized Factorio blueprint/book JSON payload."""

    raw = json.dumps(payload, separators=(",", ":")).encode()
    return "0" + base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")
