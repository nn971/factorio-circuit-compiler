"""Adapt compiled semantic Event inputs to the exact-overlap device anchor ABI.

The physical Event lowerer represents one external Event as two ordinary circuit input ports:
``<name>`` carries the payload and ``<name>__valid`` carries a one-tick activation token.  This
module is the typed bridge between that split physical ABI and a reusable device whose two ports are
explicitly marked ``TemporalModality.EVENT``.

Unlike the generic compiled-anchor adapter, Event modality is accepted only after checking the
original semantic module.  This prevents an arbitrary Level input from being relabelled as Event.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from factorio_circuit.compiler import CompilationResult
from factorio_circuit.devices.anchors import AnchoredBlueprint, AnchorSpec, BoundAnchor
from factorio_circuit.devices.compiled_anchors import (
    CompiledAnchorBinding,
    compiled_module_as_anchored_blueprint,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality


@dataclass(frozen=True, slots=True)
class CompiledEventAnchorBinding:
    """Expose one semantic Event input as aligned payload + valid anchors."""

    event: str
    payload_spec: AnchorSpec
    payload_position: tuple[float, float]
    valid_spec: AnchorSpec
    valid_position: tuple[float, float]

    def __post_init__(self) -> None:
        if not self.event:
            raise ValueError("compiled Event binding requires a non-empty event name")
        if self.payload_spec.direction is not DevicePortDirection.INPUT:
            raise ValueError("compiled Event payload anchor must be an INPUT")
        if self.valid_spec.direction is not DevicePortDirection.INPUT:
            raise ValueError("compiled Event valid anchor must be an INPUT")
        if self.payload_spec.modality is not TemporalModality.EVENT:
            raise ValueError("compiled Event payload anchor must use Event modality")
        if self.valid_spec.modality is not TemporalModality.EVENT:
            raise ValueError("compiled Event valid anchor must use Event modality")
        if self.valid_spec.payload_shape is not PayloadShape.SCALAR:
            raise ValueError("compiled Event valid anchor must be scalar")
        if self.valid_spec.signal is None:
            raise ValueError("compiled Event valid anchor requires a fixed external signal")
        if self.payload_position == self.valid_position:
            raise ValueError("compiled Event payload and valid anchors must be distinct")


def compiled_event_inputs_as_anchored_blueprint(
    result: CompilationResult,
    bindings: tuple[CompiledEventAnchorBinding, ...],
    *,
    label: str | None = None,
) -> AnchoredBlueprint:
    """Materialize exact-overlap anchors for one or more declared semantic Event inputs.

    The existing compiled-anchor machinery performs all electrical isolation, signal renaming, and
    relay routing. We invoke it with temporary Level specs because the already-lowered payload and
    valid ports are ordinary physical circuit inputs. After semantic provenance and pair
    completeness are proven here, only the exported anchor metadata is restored to Event modality.
    """

    if not bindings:
        raise ValueError("compiled Event anchoring requires at least one binding")
    event_names = [binding.event for binding in bindings]
    if len(set(event_names)) != len(event_names):
        raise ValueError("one semantic Event input cannot be anchored more than once")

    sources = {source.name: source for source in result.semantic_ir.event_inputs}
    ordinary: list[CompiledAnchorBinding] = []
    event_specs: dict[str, AnchorSpec] = {}

    for binding in bindings:
        try:
            source = sources[binding.event]
        except KeyError as exc:
            raise ValueError(
                f"compiled module has no semantic Event input {binding.event!r}"
            ) from exc
        if source.payload_shape is not binding.payload_spec.payload_shape:
            raise ValueError(
                f"semantic Event {binding.event!r} carries {source.payload_shape.value} payload, "
                f"anchor requires {binding.payload_spec.payload_shape.value}"
            )

        payload_level = replace(binding.payload_spec, modality=TemporalModality.LEVEL)
        valid_level = replace(binding.valid_spec, modality=TemporalModality.LEVEL)
        ordinary.extend(
            (
                CompiledAnchorBinding(binding.event, payload_level, binding.payload_position),
                CompiledAnchorBinding(
                    f"{binding.event}__valid",
                    valid_level,
                    binding.valid_position,
                ),
            )
        )
        event_specs[binding.payload_spec.name] = binding.payload_spec
        event_specs[binding.valid_spec.name] = binding.valid_spec

    anchored = compiled_module_as_anchored_blueprint(
        result,
        tuple(ordinary),
        label=label,
    )
    restored = tuple(
        BoundAnchor(
            event_specs.get(anchor.name, anchor.spec),
            anchor.entity_number,
            anchor.connector_id,
            anchor.position,
        )
        for anchor in anchored.anchors
    )
    return AnchoredBlueprint(anchored.blueprint, restored, anchored.label)


def event_valid_signal(name: str = "signal-A") -> SignalId:
    """Return a conventional fixed virtual lane suitable for a device Event-valid pulse."""

    if not name:
        raise ValueError("Event valid signal name must be non-empty")
    return SignalId("virtual", name)


__all__ = [
    "CompiledEventAnchorBinding",
    "compiled_event_inputs_as_anchored_blueprint",
    "event_valid_signal",
]
