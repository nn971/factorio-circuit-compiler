"""Assembler mall worker compiled directly against the reusable AssemblerDevice anchor protocol.

This is intentionally separate from ``manual_controller.py``.  The old worker exposed feeder- and
acknowledgement-specific ports; this worker speaks only the generic device protocol and lets the
AssemblerDevice own its requester, inserters, machine status, and provider chest.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Final

from factorio_circuit import Circuit, ModuleInterface, SignalId, compile_module
from factorio_circuit.devices import (
    ASSEMBLER_ENABLE_SIGNAL,
    ASSEMBLER_WORKING_SIGNAL,
    AnchorBinding,
    AnchorSpec,
    AnchoredBlueprint,
    AssemblerDevice,
    BoundAnchor,
    compose_anchored_blueprints,
    device_as_anchored_blueprint,
)
from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.devices.compiled_anchors import (
    CompiledAnchorBinding,
    compiled_module_as_anchored_blueprint,
)
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality
from factorio_circuit.synthesis.placement import PlacementOptions

MALL_DISPATCH: Final = SignalId("virtual", "signal-D")
MALL_LAUNCH: Final = SignalId("virtual", "signal-L")

MODE_FILL: Final = SignalId("virtual", "signal-C")
MODE_RUN: Final = SignalId("virtual", "signal-T")
SEEN: Final = SignalId("virtual", "signal-S")
STARTED: Final = SignalId("virtual", "signal-X")

WORKER_ACCEPTED: Final = SignalId("virtual", "signal-A")
WORKER_BUSY: Final = SignalId("virtual", "signal-B")
WORKER_FILLING: Final = SignalId("virtual", "signal-C")
WORKER_RUNNING: Final = SignalId("virtual", "signal-R")
WORKER_ARMED: Final = SignalId("virtual", "signal-S")

IRON_GEAR: Final = SignalId("item", "iron-gear-wheel")
IRON_PLATE: Final = SignalId("item", "iron-plate")

INITIAL_SOCKET_X: Final = 48.0
SOCKET_STEP: Final = 16.0
SOCKET_CORRIDOR: Final = 6.0
DEVICE_CLEARANCE: Final = 10.0
ROUTE_CLEARANCE: Final = 4.0
PROBE_VERSION: Final = 562954249306113


def build_anchored_assembler_worker() -> Circuit:
    """Build one reservation stage plus a FILL -> RUN one-craft worker state machine."""

    circuit = Circuit("anchored_assembler_worker")
    available = circuit.signals("available_in")
    control = circuit.signals("control_in")
    job_recipe = circuit.signals("job_recipe")

    # These are observations produced by AssemblerDevice RED output anchors.
    ingredients = circuit.signals("ingredients")
    requester_contents = circuit.signals("requester_contents")
    working = circuit.input("working") != 0

    dispatch = control.signal(MALL_DISPATCH) != 0
    launch = control.signal(MALL_LAUNCH) != 0

    request_missing = (ingredients - available).positive().any()
    accepted = (
        dispatch
        * job_recipe.any()
        * ingredients.any()
        * request_missing.logical_not()
    )
    remaining = available - ingredients.gate(accepted)

    fill_token = circuit.constant_signals({MODE_FILL: 1})
    run_token = circuit.constant_signals({MODE_RUN: 1})
    seen_token = circuit.constant_signals({SEEN: 1})
    started_token = circuit.constant_signals({STARTED: 1})

    mode = circuit.freeze("mode")
    seen = circuit.freeze("seen")
    held_request = circuit.freeze("held_request")
    held_recipe = circuit.freeze("held_recipe")
    started = circuit.freeze("started")

    old_mode = mode.sample()
    old_seen = seen.sample()
    old_request = held_request.sample()
    old_recipe = held_recipe.sample()
    old_started = started.sample()

    filling = old_mode.signal(MODE_FILL) != 0
    running = old_mode.signal(MODE_RUN) != 0
    busy = filling | running
    idle = busy.logical_not()
    already_seen = old_seen.signal(SEEN) != 0
    has_started = old_started.signal(STARTED) != 0

    start = (
        launch
        * accepted
        * idle
        * already_seen.logical_not()
        * working.logical_not()
    )

    # One dispatch cycle may launch this worker at most once.  Rearming requires D to fall.
    clear_seen = already_seen * dispatch.logical_not()
    seen.set(seen_token.gate(start), when=start | clear_seen)

    held_request.set(ingredients, when=start)
    held_recipe.set(job_recipe, when=start)

    missing_in_requester = (old_request - requester_contents).positive().any()
    loaded = filling * old_request.any() * missing_in_requester.logical_not()

    became_working = running * working * has_started.logical_not()
    done = running * has_started * working.logical_not()

    mode_change = start | loaded | done
    next_mode = fill_token.gate(start) + run_token.gate(loaded)
    mode.set(next_mode, when=mode_change)

    started_change = became_working | done
    started.set(started_token.gate(became_working), when=started_change)

    # The selected recipe is live before reservation so the device can advertise ingredients, then
    # frozen for the duration of the transaction so a scheduler cannot retarget a running assembler.
    selected_recipe = job_recipe.gate(idle) + old_recipe.gate(busy)

    circuit.output("remaining_out", remaining)
    circuit.output("control_out", control)
    circuit.output("recipe", selected_recipe)
    circuit.output("requester_demand", old_request.gate(filling))
    circuit.output("enable", running)
    circuit.output("accepted", accepted)
    circuit.output("busy", busy)
    circuit.output("filling", filling)
    circuit.output("running", running)
    circuit.output("armed", already_seen.logical_not())
    return circuit


# Compiler marker geometry.  Device-facing markers sit on the controller's right edge.  Their
# external anchors are farther right, where the translated AssemblerDevice lives.
# Output-observation
# routes use explicit perimeter waypoints so the adapter never crosses the device internals.
def _worker_interface(socket_x: float) -> ModuleInterface:
    """Place device-facing compiler markers on one adjustable right-side socket column."""

    return ModuleInterface(
        inputs={
            "available_in": (8.0, 4.0),
            "control_in": (8.0, 10.0),
            "job_recipe": (8.0, 16.0),
            "ingredients": (socket_x, 6.5),
            "working": (socket_x, 11.5),
            "requester_contents": (socket_x, 17.5),
        },
        outputs={
            "remaining_out": (8.0, 22.0),
            "control_out": (16.0, 22.0),
            "recipe": (socket_x, 5.5),
            "enable": (socket_x, 10.5),
            "requester_demand": (socket_x, 15.5),
            "accepted": (16.0, 26.0),
            "busy": (24.0, 26.0),
            "filling": (32.0, 26.0),
            "running": (40.0, 26.0),
            "armed": (socket_x, 26.0),
        },
    )


def _compiler_port_marker_ids(result) -> set[int]:
    return {
        *(port.marker_entity for port in result.physical_circuit.inputs),
        *(port.marker_entity for port in result.physical_circuit.outputs),
    }


def _blueprint_positions(result) -> dict[int, tuple[float, float]]:
    blueprint = result.blueprint_json["blueprint"]
    return {
        int(entity["entity_number"]): (
            float(entity["position"]["x"]),
            float(entity["position"]["y"]),
        )
        for entity in blueprint.get("entities", [])
    }


def _socket_corridor_is_clear(result, socket_x: float) -> bool:
    """Require all implementation entities/relays to stay left of the socket corridor."""

    marker_ids = _compiler_port_marker_ids(result)
    positions = _blueprint_positions(result)
    return all(
        x <= socket_x - SOCKET_CORRIDOR
        for entity_id, (x, _y) in positions.items()
        if entity_id not in marker_ids
    )


def _compile_with_clear_socket():
    socket_x = INITIAL_SOCKET_X
    for _attempt in range(6):
        result = compile_module(
            build_anchored_assembler_worker(),
            _worker_interface(socket_x),
            placement=PlacementOptions(iterations=0, restarts=3, target_fill=0.58),
        )
        if _socket_corridor_is_clear(result, socket_x):
            return result, socket_x
        socket_x += SOCKET_STEP
    raise ValueError("could not synthesize AssemblerWorker with a clear device socket corridor")



def _level_vector(name: str, direction: DevicePortDirection, wire: WireColor) -> AnchorSpec:
    return AnchorSpec(name, direction, PayloadShape.VECTOR, TemporalModality.LEVEL, wire)


def _level_scalar(
    name: str,
    direction: DevicePortDirection,
    wire: WireColor,
    signal: SignalId,
) -> AnchorSpec:
    return AnchorSpec(name, direction, PayloadShape.SCALAR, TemporalModality.LEVEL, wire, signal)


def _device_bounds(device) -> tuple[float, float, float, float]:
    positions = [
        (float(entity["position"]["x"]), float(entity["position"]["y"]))
        for entity in device.blueprint["entities"]
    ]
    xs = [x for x, _ in positions]
    ys = [y for _, y in positions]
    return min(xs), min(ys), max(xs), max(ys)


def _device_offset(socket_x: float, device) -> tuple[float, float]:
    min_x, _min_y, _max_x, _max_y = _device_bounds(device)
    return (socket_x + DEVICE_CLEARANCE - min_x, 0.0)


def _device_position(device, offset: tuple[float, float], name: str) -> tuple[float, float]:
    position = device.port(name).endpoint.position
    return position[0] + offset[0], position[1] + offset[1]


def _observation_route(
    device,
    offset: tuple[float, float],
    *,
    lane: int,
) -> tuple[tuple[float, float], ...]:
    min_x, min_y, max_x, _max_y = _device_bounds(device)
    left = min_x + offset[0] - ROUTE_CLEARANCE
    right = max_x + offset[0] + ROUTE_CLEARANCE
    top = min_y + offset[1] - ROUTE_CLEARANCE - lane * 2.0
    return ((left, top), (right, top))


def _worker_anchor_bindings(
    socket_x: float, device, offset: tuple[float, float]
) -> tuple[CompiledAnchorBinding, ...]:
    # Scheduler/probe side. These remain on the worker's left side.
    scheduler = (
        CompiledAnchorBinding(
            "available_in",
            _level_vector("available_in", DevicePortDirection.INPUT, WireColor.RED),
            (0.0, 4.0),
        ),
        CompiledAnchorBinding(
            "control_in",
            _level_vector("control_in", DevicePortDirection.INPUT, WireColor.RED),
            (0.0, 10.0),
        ),
        CompiledAnchorBinding(
            "job_recipe",
            _level_vector("job_recipe", DevicePortDirection.INPUT, WireColor.RED),
            (0.0, 16.0),
        ),
    )

    # Device command side: direct through the compiler-verified empty socket corridor.
    commands = (
        CompiledAnchorBinding(
            "recipe",
            _level_vector("recipe", DevicePortDirection.OUTPUT, WireColor.GREEN),
            _device_position(device, offset, "recipe"),
        ),
        CompiledAnchorBinding(
            "enable",
            _level_scalar(
                "enable", DevicePortDirection.OUTPUT, WireColor.GREEN, ASSEMBLER_ENABLE_SIGNAL
            ),
            _device_position(device, offset, "enable"),
        ),
        CompiledAnchorBinding(
            "requester_demand",
            _level_vector("requester_demand", DevicePortDirection.OUTPUT, WireColor.GREEN),
            _device_position(device, offset, "requester_demand"),
        ),
    )

    # Device observations live on the far/right side of the device. Route adapter relays around
    # the top of the measured device bounds instead of through its machine/chest footprint.
    observations = (
        CompiledAnchorBinding(
            "ingredients",
            _level_vector("ingredients", DevicePortDirection.INPUT, WireColor.RED),
            _device_position(device, offset, "ingredients"),
            route=_observation_route(device, offset, lane=0),
        ),
        CompiledAnchorBinding(
            "working",
            _level_scalar(
                "working", DevicePortDirection.INPUT, WireColor.RED, ASSEMBLER_WORKING_SIGNAL
            ),
            _device_position(device, offset, "working"),
            route=_observation_route(device, offset, lane=1),
        ),
        CompiledAnchorBinding(
            "requester_contents",
            _level_vector("requester_contents", DevicePortDirection.INPUT, WireColor.RED),
            _device_position(device, offset, "requester_contents"),
            route=_observation_route(device, offset, lane=2),
        ),
    )

    diagnostics = (
        CompiledAnchorBinding(
            "accepted",
            _level_scalar(
                "accepted", DevicePortDirection.OUTPUT, WireColor.RED, WORKER_ACCEPTED
            ),
            (16.0, 31.0),
        ),
        CompiledAnchorBinding(
            "busy",
            _level_scalar("busy", DevicePortDirection.OUTPUT, WireColor.RED, WORKER_BUSY),
            (24.0, 31.0),
        ),
        CompiledAnchorBinding(
            "filling",
            _level_scalar(
                "filling", DevicePortDirection.OUTPUT, WireColor.RED, WORKER_FILLING
            ),
            (32.0, 31.0),
        ),
        CompiledAnchorBinding(
            "running",
            _level_scalar(
                "running", DevicePortDirection.OUTPUT, WireColor.RED, WORKER_RUNNING
            ),
            (40.0, 31.0),
        ),
        CompiledAnchorBinding(
            "armed",
            _level_scalar("armed", DevicePortDirection.OUTPUT, WireColor.RED, WORKER_ARMED),
            (socket_x, 31.0),
        ),
    )
    return (*scheduler, *commands, *observations, *diagnostics)



def compile_anchored_assembler_worker(
    *, modules: tuple[str, ...] = ()
) -> tuple[AnchoredBlueprint, tuple[float, float]]:
    """Compile the worker, prove a clear socket corridor, and normalize its public anchors."""

    result, socket_x = _compile_with_clear_socket()
    raw_device = AssemblerDevice(modules=modules, label="AssemblerWorker device").build()
    offset = _device_offset(socket_x, raw_device)
    worker = compiled_module_as_anchored_blueprint(
        result,
        _worker_anchor_bindings(socket_x, raw_device, offset),
        label="Anchored AssemblerWorker controller",
    )
    return worker, offset


def build_worker_with_device(*, modules: tuple[str, ...] = ()) -> AnchoredBlueprint:
    """Compose a compiled worker with one opaque AssemblerDevice using six exact-overlap anchors."""

    worker, offset = compile_anchored_assembler_worker(modules=modules)
    device = device_as_anchored_blueprint(
        AssemblerDevice(modules=modules, label="AssemblerWorker device").build(),
        label="AssemblerWorker device",
    )
    bindings = tuple(
        AnchorBinding(name, name)
        for name in (
            "recipe",
            "enable",
            "requester_demand",
            "ingredients",
            "requester_contents",
            "working",
        )
    )
    composed = compose_anchored_blueprints(
        worker,
        device,
        bindings=bindings,
        right_offset=offset,
        label="AssemblerWorker + AssemblerDevice",
    )
    return AnchoredBlueprint(composed.blueprint, composed.anchors, "AssemblerWorker + device")


def _constant_behavior(signals: tuple[tuple[SignalId, int], ...]) -> dict[str, object]:
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


def _probe_source(
    target: AnchoredBlueprint,
    name: str,
    signals: tuple[tuple[SignalId, int], ...],
    description: str,
) -> AnchoredBlueprint:
    target_anchor = target.anchor(name)
    entity: dict[str, object] = {
        "entity_number": 1,
        "name": "constant-combinator",
        "position": {"x": target_anchor.position[0], "y": target_anchor.position[1]},
        "player_description": description,
        "control_behavior": _constant_behavior(signals),
    }
    return AnchoredBlueprint(
        {
            "item": "blueprint",
            "version": PROBE_VERSION,
            "entities": [entity],
            "wires": [],
        },
        (
            BoundAnchor(
                AnchorSpec(
                    f"{name}_source",
                    DevicePortDirection.OUTPUT,
                    target_anchor.spec.payload_shape,
                    target_anchor.spec.modality,
                    target_anchor.spec.wire,
                    target_anchor.spec.signal,
                ),
                1,
                target_anchor.connector_id,
                target_anchor.position,
            ),
        ),
        f"probe source {name}",
    )


def _bind_source(
    target: AnchoredBlueprint,
    name: str,
    signals: tuple[tuple[SignalId, int], ...],
    description: str,
) -> AnchoredBlueprint:
    source = _probe_source(target, name, signals, description)
    composed = compose_anchored_blueprints(
        target,
        source,
        bindings=(AnchorBinding(name, f"{name}_source"),),
    )
    return AnchoredBlueprint(composed.blueprint, composed.anchors, target.label)


def build_assembler_worker_probe() -> Blueprint:
    """Build a self-contained worker+device probe with editable D/L and fake stock."""

    component = build_worker_with_device(modules=("productivity-module-3",) * 4)
    component = _bind_source(
        component,
        "available_in",
        ((IRON_PLATE, 100),),
        "PROBE available stock — fixed 100 iron plates",
    )
    component = _bind_source(
        component,
        "job_recipe",
        ((IRON_GEAR, 1),),
        "PROBE job recipe — iron gear wheel",
    )
    component = _bind_source(
        component,
        "control_in",
        ((MALL_DISPATCH, 0), (MALL_LAUNCH, 0)),
        "PROBE CONTROL D/L — EDIT HERE",
    )
    blueprint = deepcopy(component.blueprint)
    blueprint["label"] = "AssemblerWorker anchored probe — one iron-gear transaction"
    blueprint["icons"] = [{"signal": {"name": "assembling-machine-3"}, "index": 1}]
    return blueprint


def generate_assembler_worker_probe_string() -> str:
    return encode_blueprint(build_assembler_worker_probe())


def main() -> None:
    print(generate_assembler_worker_probe_string())


if __name__ == "__main__":
    main()
