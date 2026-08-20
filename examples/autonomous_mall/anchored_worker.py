"""Compiled autonomous-mall worker bound directly to the AssemblerDevice anchor protocol."""

from __future__ import annotations

from typing import Final

from factorio_circuit import Circuit, ModuleInterface, SignalId, compile_module
from factorio_circuit.devices import (
    AnchorBinding,
    AnchorSpec,
    AssemblerDevice,
    DevicePortDirection,
    ModuleAnchorBinding,
    compiled_module_as_anchored_blueprint,
    compose_anchored_blueprints,
    socketize_assembler_device,
)
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality
from factorio_circuit.synthesis.placement import PlacementOptions

TILE_WIDTH: Final = 48
TILE_HEIGHT: Final = 48
DEVICE_OFFSET: Final = (0.0, 56.0)

DISPATCH: Final = SignalId("virtual", "signal-D")
LAUNCH: Final = SignalId("virtual", "signal-L")
MODE_FILL: Final = SignalId("virtual", "signal-C")
MODE_RUN: Final = SignalId("virtual", "signal-R")
MODE_WAIT: Final = SignalId("virtual", "signal-T")
SEEN: Final = SignalId("virtual", "signal-S")

# These positions mirror the socketized AssemblerDevice, translated by DEVICE_OFFSET.
_DEVICE_ANCHOR_POSITIONS: Final = {
    "recipe": (1.5, 57.5),
    "enable": (4.5, 57.5),
    "requester_demand": (7.5, 57.5),
    "ingredients": (10.5, 57.5),
    "requester_contents": (13.5, 57.5),
    "working": (19.5, 57.5),
}

WORKER_INTERFACE: Final = ModuleInterface(
    inputs={
        "available_in": (4.0, 3.0),
        "control_in": (11.0, 3.0),
        "job_recipe": (18.0, 3.0),
        "ingredients": (10.5, 44.0),
        "requester_contents": (13.5, 44.0),
        "working": (19.5, 44.0),
    },
    outputs={
        "recipe": (1.5, 44.0),
        "enable": (4.5, 44.0),
        "requester_demand": (7.5, 44.0),
        "remaining_out": (40.0, 3.0),
        "accepted": (28.0, 44.0),
        "busy": (32.0, 44.0),
        "filling": (36.0, 44.0),
        "running": (40.0, 44.0),
        "waiting": (44.0, 44.0),
    },
    grid_size=(TILE_WIDTH, TILE_HEIGHT),
)


def build_assembler_worker() -> Circuit:
    """Build one reservation + one-craft transaction controller for AssemblerDevice.

    The recipe is driven continuously so the device can expose ingredients before launch. Once a
    transaction starts, the worker requests one ingredient vector and waits for the requester chest
    to contain it completely. It then drops the logistic request, enables the device, observes
    ``working`` rise, and keeps the device enabled until ``working`` falls again. The observed
    high->low transition is the one-craft completion acknowledgement; the worker therefore needs no
    one-tick ``finished`` Event for this Level-only v1 controller.
    """

    circuit = Circuit("anchored_assembler_worker")
    available = circuit.signals("available_in")
    control = circuit.signals("control_in")
    job_recipe = circuit.signals("job_recipe")
    ingredients = circuit.signals("ingredients")
    requester_contents = circuit.signals("requester_contents")
    working = circuit.input("working") != 0

    dispatch = control.signal(DISPATCH) != 0
    launch = control.signal(LAUNCH) != 0

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
    wait_token = circuit.constant_signals({MODE_WAIT: 1})
    seen_token = circuit.constant_signals({SEEN: 1})

    mode = circuit.freeze("mode")
    seen = circuit.freeze("seen")
    held_request = circuit.freeze("held_request")

    old_mode = mode.sample()
    old_seen = seen.sample()
    old_request = held_request.sample()

    filling = old_mode.signal(MODE_FILL) != 0
    running = old_mode.signal(MODE_RUN) != 0
    waiting = old_mode.signal(MODE_WAIT) != 0
    idle = old_mode.any().logical_not()
    already_seen = old_seen.signal(SEEN) != 0

    start = (
        launch
        * accepted
        * idle
        * already_seen.logical_not()
        * working.logical_not()
    )
    # A dispatch cycle is re-armed only when D is dropped. Transient changes to accepted while a
    # device is consuming ingredients must never arm a second transaction under a held D/L command.
    clear_seen = already_seen * dispatch.logical_not()
    seen_change = start | clear_seen
    seen.set(seen_token.gate(start), when=seen_change)
    held_request.set(ingredients, when=start)

    missing_in_requester = (old_request - requester_contents).positive().any()
    loaded = filling * old_request.any() * missing_in_requester.logical_not()
    saw_working = running * working
    worker_done = waiting * working.logical_not()

    mode_change = start | loaded | saw_working | worker_done
    next_mode = (
        fill_token.gate(start)
        + run_token.gate(loaded)
        + wait_token.gate(saw_working)
    )
    mode.set(next_mode, when=mode_change)

    enabled = running | waiting
    circuit.output("recipe", job_recipe)
    circuit.output("enable", enabled)
    circuit.output("requester_demand", old_request.gate(filling))
    circuit.output("remaining_out", remaining)
    circuit.output("accepted", accepted)
    circuit.output("busy", filling | running | waiting)
    circuit.output("filling", filling)
    circuit.output("running", running)
    circuit.output("waiting", waiting)
    return circuit


def compile_assembler_worker():
    return compile_module(
        build_assembler_worker(),
        WORKER_INTERFACE,
        placement=PlacementOptions(iterations=0, restarts=3, target_fill=0.60),
    )


def _worker_anchor(name: str, *, direction: DevicePortDirection) -> AnchorSpec:
    if name in {"recipe", "requester_demand", "ingredients", "requester_contents"}:
        shape = PayloadShape.VECTOR
        signal = None
    elif name == "enable":
        shape = PayloadShape.SCALAR
        signal = SignalId("virtual", "signal-E")
    elif name == "working":
        shape = PayloadShape.SCALAR
        signal = SignalId("virtual", "signal-W")
    else:  # pragma: no cover - defensive for future socket growth.
        raise KeyError(name)
    wire = WireColor.GREEN if direction is DevicePortDirection.OUTPUT else WireColor.RED
    return AnchorSpec(
        name,
        direction,
        shape,
        TemporalModality.LEVEL,
        wire,
        signal,
    )


def worker_as_anchored_blueprint(result=None):
    """Expose the six Level AssemblerDevice ports as stable worker anchors."""

    compiled = result or compile_assembler_worker()
    specs = (
        ("recipe", DevicePortDirection.OUTPUT),
        ("enable", DevicePortDirection.OUTPUT),
        ("requester_demand", DevicePortDirection.OUTPUT),
        ("ingredients", DevicePortDirection.INPUT),
        ("requester_contents", DevicePortDirection.INPUT),
        ("working", DevicePortDirection.INPUT),
    )
    bindings = tuple(
        ModuleAnchorBinding(
            name,
            _worker_anchor(name, direction=direction),
            _DEVICE_ANCHOR_POSITIONS[name],
            (_DEVICE_ANCHOR_POSITIONS[name][0] + 0.5, 50.5),
        )
        for name, direction in specs
    )
    return compiled_module_as_anchored_blueprint(
        compiled,
        bindings,
        label="Anchored assembler worker",
    )


def build_anchored_worker_device(
    *,
    modules: tuple[str, ...] = (),
):
    """Compile the worker and bind it to a socketized reusable AssemblerDevice."""

    worker = worker_as_anchored_blueprint()
    device = socketize_assembler_device(
        AssemblerDevice(modules=modules, label="AssemblerDevice mall worker").build()
    ).anchored()
    names = tuple(_DEVICE_ANCHOR_POSITIONS)
    return compose_anchored_blueprints(
        worker,
        device,
        bindings=tuple(AnchorBinding(name, name) for name in names),
        right_offset=DEVICE_OFFSET,
    )
