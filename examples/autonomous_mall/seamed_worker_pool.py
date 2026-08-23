"""Seam-composed physical worker pool for the deterministic autonomous mall.

This is the physical successor to :mod:`examples.autonomous_mall.worker_pool`.
The old module remains the compact monolithic semantic reference.  Here the same one-craft worker
protocol is packaged as reusable bounded components:

* one dispatch head owns the external four-phase offer latch;
* N identical worker cells form a north/south packet-and-ledger bus;
* one tail returns ``blocked`` when a probe token traverses every worker.

Only one probe token is in flight at a time.  This stop-and-wait rule is intentional: separately
compiled component seams have physical latency, so holding a combinational first-free token high
until a distant acknowledgement returns could duplicate an offer if an upstream worker became idle
in the meantime. A one-shot probe is either consumed by exactly one idle worker or reaches the
tail; the head waits for that response before retrying.

Each worker controller has three whole-vector registers (recipe, reservation, promise), and its east
seam attaches to a real :class:`factorio_circuit.devices.AssemblerDevice`.  A short device-owned
working-signal route moves that one observation onto the west mall seam; unlike the previous probe,
there is no composer-generated route around the assembler footprint.

Compiled controller seams deliberately live in four-tile guard bands outside the annealer's dense
body envelope.  The compiler marker sits at the inner edge, the isolation adapter occupies the
middle, and the exact-overlap anchor sits on the outer component boundary.  This keeps public docks
visible and prevents post-placement anchor adapters from being buried under unrelated logic.  A
separate west routing strip absorbs ordinary synthesis spill without weakening those seam corridors.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from typing import Final

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.devices import (
    ASSEMBLER_ENABLE_SIGNAL,
    ASSEMBLER_WORKING_SIGNAL,
    AnchoredBlueprint,
    AnchorSpec,
    AssemblerDevice,
    BoundAnchor,
    BoundarySlot,
    CompiledAnchorBinding,
    ComponentFootprint,
    ComponentSeam,
    ComponentSide,
    ConstrainedComponent,
    compiled_module_as_anchored_blueprint,
    compose_component_seams,
    device_as_anchored_blueprint,
)
from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.devices.assembler import FACTORIO_BLUEPRINT_VERSION
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.frontend import Circuit
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality

from .worker_pool import (
    BUSY_COUNT_SIGNAL,
    COMPLETION_COUNT_SIGNAL,
    OFFER_ACCEPTED_SIGNAL,
    OFFER_BLOCKED_SIGNAL,
    OFFER_VALID_SIGNAL,
)

_SEEN_SIGNAL: Final = SignalId("virtual", "signal-S")
_WAITING_SIGNAL: Final = SignalId("virtual", "signal-T")
_EACH: Final = SignalId("virtual", "signal-each")

_PITCH: Final = 0.5
_CONTROLLER_BODY_WIDTH: Final = 32.0
_CONTROLLER_BODY_HEIGHT: Final = 24.0
_SEAM_GUARD: Final = 4.0
_CONTROLLER_MIN_Y: Final = -_SEAM_GUARD
_CONTROLLER_MAX_X: Final = _CONTROLLER_BODY_WIDTH + _SEAM_GUARD
_CONTROLLER_MAX_Y: Final = _CONTROLLER_BODY_HEIGHT + _SEAM_GUARD
_BUS_X: Final = (3.0, 6.0, 9.0, 12.0, 15.0, 18.0, 21.0, 24.0, 27.0, 30.0)


@dataclass(frozen=True, slots=True)
class _BusLane:
    name: str
    forward: bool
    shape: PayloadShape
    wire: WireColor
    signal: SignalId | None = None


# Keep request/response scalars close together physically; the tail therefore converts V -> B with
# one arithmetic combinator and no relay chain.
_BUS_LANES: Final = (
    _BusLane("offer_valid", True, PayloadShape.SCALAR, WireColor.GREEN, OFFER_VALID_SIGNAL),
    _BusLane("blocked", False, PayloadShape.SCALAR, WireColor.RED, OFFER_BLOCKED_SIGNAL),
    _BusLane("accepted", False, PayloadShape.SCALAR, WireColor.RED, OFFER_ACCEPTED_SIGNAL),
    _BusLane("busy_count", False, PayloadShape.SCALAR, WireColor.RED, BUSY_COUNT_SIGNAL),
    _BusLane(
        "completion_count",
        False,
        PayloadShape.SCALAR,
        WireColor.RED,
        COMPLETION_COUNT_SIGNAL,
    ),
    _BusLane("offer_recipe", True, PayloadShape.VECTOR, WireColor.GREEN),
    _BusLane("offer_inputs", True, PayloadShape.VECTOR, WireColor.GREEN),
    _BusLane("offer_product", True, PayloadShape.VECTOR, WireColor.GREEN),
    _BusLane("reserved", False, PayloadShape.VECTOR, WireColor.RED),
    _BusLane("promised", False, PayloadShape.VECTOR, WireColor.RED),
)

_EXTERNAL_NAMES: Final = {
    "offer_valid": "offer_valid",
    "blocked": "offer_blocked",
    "accepted": "offer_accepted",
    "busy_count": "busy_count",
    "completion_count": "completion_count",
    "offer_recipe": "offer_recipe",
    "offer_inputs": "offer_inputs",
    "offer_product": "offer_product",
    "reserved": "reserved",
    "promised": "promised",
}


@dataclass(frozen=True, slots=True)
class _Dock:
    port: str
    anchor: str
    side: ComponentSide
    slot: int
    direction: DevicePortDirection
    shape: PayloadShape
    wire: WireColor
    signal: SignalId | None = None

    def spec(self) -> AnchorSpec:
        return AnchorSpec(
            self.anchor,
            self.direction,
            self.shape,
            TemporalModality.LEVEL,
            self.wire,
            self.signal,
        )


def _controller_footprint() -> ComponentFootprint:
    return ComponentFootprint(
        0.0,
        _CONTROLLER_MIN_Y,
        _CONTROLLER_MAX_X,
        _CONTROLLER_MAX_Y,
        _PITCH,
    )


def _slot_x(index: int) -> int:
    return round(_BUS_X[index] / _PITCH)


def _slot_y(y: float) -> int:
    """Return the east/west boundary slot for one body-relative y coordinate."""

    return round((y - _CONTROLLER_MIN_Y) / _PITCH)


def _bus_docks(
    *,
    side: ComponentSide,
    seam_prefix: str,
    port_prefix: str,
    forward_out: bool,
) -> tuple[_Dock, ...]:
    result: list[_Dock] = []
    for index, lane in enumerate(_BUS_LANES):
        if lane.forward:
            direction = DevicePortDirection.OUTPUT if forward_out else DevicePortDirection.INPUT
        else:
            direction = DevicePortDirection.INPUT if forward_out else DevicePortDirection.OUTPUT
        result.append(
            _Dock(
                f"{port_prefix}{lane.name}",
                f"{seam_prefix}{lane.name}",
                side,
                _slot_x(index),
                direction,
                lane.shape,
                lane.wire,
                lane.signal,
            )
        )
    return tuple(result)


def _external_head_docks() -> tuple[_Dock, ...]:
    result: list[_Dock] = []
    for index, lane in enumerate(_BUS_LANES):
        direction = (
            DevicePortDirection.INPUT if lane.forward else DevicePortDirection.OUTPUT
        )
        result.append(
            _Dock(
                _EXTERNAL_NAMES[lane.name],
                _EXTERNAL_NAMES[lane.name],
                ComponentSide.NORTH,
                _slot_x(index),
                direction,
                lane.shape,
                lane.wire,
                lane.signal,
            )
        )
    return tuple(result)


def _device_docks() -> tuple[_Dock, ...]:
    return (
        _Dock(
            "device_recipe",
            "device_recipe",
            ComponentSide.EAST,
            _slot_y(5.5),
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            WireColor.GREEN,
        ),
        _Dock(
            "device_enable",
            "device_enable",
            ComponentSide.EAST,
            _slot_y(10.5),
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            WireColor.GREEN,
            ASSEMBLER_ENABLE_SIGNAL,
        ),
        _Dock(
            "device_working",
            "device_working",
            ComponentSide.EAST,
            _slot_y(12.5),
            DevicePortDirection.INPUT,
            PayloadShape.SCALAR,
            WireColor.RED,
            ASSEMBLER_WORKING_SIGNAL,
        ),
        _Dock(
            "device_requester_demand",
            "device_requester_demand",
            ComponentSide.EAST,
            _slot_y(15.5),
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            WireColor.GREEN,
        ),
    )


def _marker_position(
    footprint: ComponentFootprint,
    side: ComponentSide,
    slot: int,
    *,
    inset: float = _SEAM_GUARD,
) -> tuple[float, float]:
    x, y = footprint.boundary_position(side, slot)
    if side is ComponentSide.NORTH:
        return x, y + inset
    if side is ComponentSide.SOUTH:
        return x, y - inset
    if side is ComponentSide.WEST:
        return x + inset, y
    return x - inset, y


def _compiled_component(
    circuit: Circuit,
    footprint: ComponentFootprint,
    docks: tuple[_Dock, ...],
    seams: tuple[ComponentSeam, ...],
    *,
    label: str,
) -> ConstrainedComponent:
    positions = {
        dock.port: _marker_position(footprint, dock.side, dock.slot) for dock in docks
    }
    result = compile_circuit(circuit, port_positions=positions)
    bindings = tuple(
        CompiledAnchorBinding(
            dock.port,
            dock.spec(),
            footprint.boundary_position(dock.side, dock.slot),
        )
        for dock in docks
    )
    anchored = compiled_module_as_anchored_blueprint(result, bindings, label=label)
    routing_strip = ComponentFootprint(
        footprint.min_x - _SEAM_GUARD,
        footprint.min_y,
        footprint.min_x,
        footprint.max_y,
        footprint.slot_pitch,
    )
    return ConstrainedComponent(
        anchored,
        (footprint, routing_strip),
        tuple(BoundarySlot(dock.anchor, dock.side, dock.slot) for dock in docks),
        seams,
    )


def build_dispatch_head() -> Circuit:
    """Build the stop-and-wait transaction head for the external four-phase offer ABI."""

    circuit = Circuit("deterministic_mall_dispatch_head")
    raw_valid = circuit.input("offer_valid") != 0
    recipe = circuit.signals("offer_recipe")
    inputs = circuit.signals("offer_inputs")
    product = circuit.signals("offer_product")

    downstream_blocked = circuit.input("bus_blocked") != 0
    downstream_accepted = circuit.input("bus_accepted") != 0
    downstream_busy = circuit.input("bus_busy_count")
    downstream_completion = circuit.input("bus_completion_count")
    downstream_reserved = circuit.signals("bus_reserved")
    downstream_promised = circuit.signals("bus_promised")

    seen_reg = circuit.freeze("offer_seen")
    waiting_reg = circuit.freeze("probe_waiting")
    blocked_reg = circuit.freeze("offer_blocked_state")
    seen = seen_reg.sample().signal(_SEEN_SIGNAL) != 0
    waiting = waiting_reg.sample().signal(_WAITING_SIGNAL) != 0
    blocked = blocked_reg.sample().signal(OFFER_BLOCKED_SIGNAL) != 0

    well_formed = recipe.any() * product.any()
    send = raw_valid * seen.logical_not() * waiting.logical_not() * well_formed
    accepted = downstream_accepted * raw_valid
    block_response = downstream_blocked * raw_valid
    response = accepted | block_response

    clear_seen = seen * raw_valid.logical_not()
    clear_waiting = waiting * raw_valid.logical_not()
    clear_blocked = blocked * raw_valid.logical_not()

    seen_reg.set(
        circuit.constant_signals({_SEEN_SIGNAL: 1}).gate(accepted),
        when=accepted | clear_seen,
    )
    waiting_reg.set(
        circuit.constant_signals({_WAITING_SIGNAL: 1}).gate(send),
        when=send | response | clear_waiting,
    )
    blocked_reg.set(
        circuit.constant_signals({OFFER_BLOCKED_SIGNAL: 1}).gate(block_response),
        when=block_response | accepted | clear_blocked,
    )

    circuit.output("bus_offer_valid", send)
    circuit.output("bus_offer_recipe", recipe.gate(send))
    circuit.output("bus_offer_inputs", inputs.gate(send))
    circuit.output("bus_offer_product", product.gate(send))

    circuit.output("reserved", downstream_reserved)
    circuit.output("promised", downstream_promised)
    circuit.output("offer_accepted", accepted)
    circuit.output(
        "offer_blocked",
        (blocked | block_response) * raw_valid * accepted.logical_not(),
    )
    circuit.output("busy_count", downstream_busy)
    circuit.output("completion_count", downstream_completion)
    return circuit


def build_worker_stage() -> Circuit:
    """Build one anonymous one-craft worker stage on the bidirectional mall bus."""

    circuit = Circuit("deterministic_mall_worker_stage")
    token = circuit.input("in_offer_valid") != 0
    recipe = circuit.signals("in_offer_recipe")
    inputs = circuit.signals("in_offer_inputs")
    product = circuit.signals("in_offer_product")

    down_blocked = circuit.input("down_blocked")
    down_accepted = circuit.input("down_accepted")
    down_busy = circuit.input("down_busy_count")
    down_completion = circuit.input("down_completion_count")
    down_reserved = circuit.signals("down_reserved")
    down_promised = circuit.signals("down_promised")
    working = circuit.input("device_working") != 0

    recipe_reg = circuit.freeze("worker_recipe_state")
    reservation_reg = circuit.freeze("worker_reservation_state")
    promise_reg = circuit.freeze("worker_promise_state")
    held_recipe = recipe_reg.sample()
    held_reservation = reservation_reg.sample()
    held_promise = promise_reg.sample()

    busy = held_promise.any()
    idle = busy.logical_not()
    starting = busy * held_recipe.any()
    waiting = busy * held_recipe.any().logical_not()
    well_formed = recipe.any() * product.any()

    claim = token * idle * well_formed
    forward = token * busy * well_formed
    started = starting * working
    finished = waiting * working.logical_not()

    recipe_reg.set(recipe.gate(claim), when=claim | started)
    reservation_reg.set(inputs.gate(claim), when=claim | finished)
    promise_reg.set(product.gate(claim), when=claim | finished)

    live_reservation = held_reservation + inputs.gate(claim)
    live_promise = held_promise + product.gate(claim)
    live_busy = busy | claim

    circuit.output("next_offer_valid", forward)
    circuit.output("next_offer_recipe", recipe.gate(forward))
    circuit.output("next_offer_inputs", inputs.gate(forward))
    circuit.output("next_offer_product", product.gate(forward))

    circuit.output("up_reserved", live_reservation + down_reserved)
    circuit.output("up_promised", live_promise + down_promised)
    circuit.output("up_accepted", down_accepted + claim)
    circuit.output("up_blocked", down_blocked)
    circuit.output("up_busy_count", down_busy + live_busy)
    circuit.output("up_completion_count", down_completion + finished)

    circuit.output("device_recipe", held_recipe.gate(starting))
    circuit.output("device_enable", busy)
    circuit.output("device_requester_demand", held_reservation.gate(starting))
    return circuit


def _head_component() -> ConstrainedComponent:
    footprint = _controller_footprint()
    external = _external_head_docks()
    south = _bus_docks(
        side=ComponentSide.SOUTH,
        seam_prefix="south_",
        port_prefix="bus_",
        forward_out=True,
    )
    docks = external + south
    return _compiled_component(
        build_dispatch_head(),
        footprint,
        docks,
        (
            ComponentSeam("external", ComponentSide.NORTH, tuple(dock.anchor for dock in external)),
            ComponentSeam("south_bus", ComponentSide.SOUTH, tuple(dock.anchor for dock in south)),
        ),
        label="Deterministic mall dispatch head",
    )


def _worker_controller_component() -> ConstrainedComponent:
    footprint = _controller_footprint()
    # Worker semantic port names differ on the reverse lanes, so build both faces explicitly while
    # retaining exactly the same physical lane order as the head and tail.
    north_ports: list[_Dock] = []
    south_ports: list[_Dock] = []
    for index, lane in enumerate(_BUS_LANES):
        slot = _slot_x(index)
        if lane.forward:
            north_port = f"in_{lane.name}"
            south_port = f"next_{lane.name}"
            north_direction = DevicePortDirection.INPUT
            south_direction = DevicePortDirection.OUTPUT
        else:
            north_port = f"up_{lane.name}"
            south_port = f"down_{lane.name}"
            north_direction = DevicePortDirection.OUTPUT
            south_direction = DevicePortDirection.INPUT
        north_ports.append(
            _Dock(
                north_port,
                f"north_{lane.name}",
                ComponentSide.NORTH,
                slot,
                north_direction,
                lane.shape,
                lane.wire,
                lane.signal,
            )
        )
        south_ports.append(
            _Dock(
                south_port,
                f"south_{lane.name}",
                ComponentSide.SOUTH,
                slot,
                south_direction,
                lane.shape,
                lane.wire,
                lane.signal,
            )
        )
    device = _device_docks()
    docks = tuple(north_ports) + tuple(south_ports) + device
    return _compiled_component(
        build_worker_stage(),
        footprint,
        docks,
        (
            ComponentSeam(
                "north_bus",
                ComponentSide.NORTH,
                tuple(dock.anchor for dock in north_ports),
            ),
            ComponentSeam(
                "south_bus",
                ComponentSide.SOUTH,
                tuple(dock.anchor for dock in south_ports),
            ),
            ComponentSeam(
                "assembler",
                ComponentSide.EAST,
                tuple(dock.anchor for dock in device),
            ),
        ),
        label="Deterministic mall worker controller",
    )


def _mall_assembler_component() -> ConstrainedComponent:
    """Expose only the four worker-facing ports of an assembler as one west seam."""

    built = AssemblerDevice(label="Deterministic mall worker").build()
    original = device_as_anchored_blueprint(built, label="assembler-worker")
    blueprint = deepcopy(original.blueprint)
    entities = blueprint.get("entities", [])
    wires = blueprint.get("wires", [])
    if not isinstance(entities, list) or not isinstance(wires, list):
        raise ValueError("assembler blueprint has invalid entity/wire containers")

    next_entity = max(int(entity["entity_number"]) for entity in entities) + 1
    working_boundary = next_entity
    relay_west = next_entity + 1
    relay_east = next_entity + 2
    entities.extend(
        (
            {
                "entity_number": working_boundary,
                "name": "constant-combinator",
                "position": {"x": 1.5, "y": 12.5},
                "player_description": "ASSEMBLER MALL PORT working — OUTPUT signal-W Level; RED",
            },
            {
                "entity_number": relay_west,
                "name": "constant-combinator",
                "position": {"x": 5.0, "y": 12.5},
                "player_description": "ASSEMBLER MALL working relay west",
            },
            {
                "entity_number": relay_east,
                "name": "constant-combinator",
                "position": {"x": 11.0, "y": 12.5},
                "player_description": "ASSEMBLER MALL working relay east",
            },
        )
    )
    original_working = original.anchor("working")
    wires.extend(
        (
            [original_working.entity_number, 1, relay_east, 1],
            [relay_west, 1, relay_east, 1],
            [working_boundary, 1, relay_west, 1],
        )
    )

    working_spec = original_working.spec
    selected = (
        original.anchor("recipe"),
        original.anchor("enable"),
        BoundAnchor(working_spec, working_boundary, 1, (1.5, 12.5)),
        original.anchor("requester_demand"),
    )
    anchored = AnchoredBlueprint(blueprint, selected, "assembler-worker-mall-seam")
    footprint = ComponentFootprint(1.5, 4.5, 20.0, 17.5, _PITCH)
    slots = (
        BoundarySlot("recipe", ComponentSide.WEST, 2),
        BoundarySlot("enable", ComponentSide.WEST, 12),
        BoundarySlot("working", ComponentSide.WEST, 16),
        BoundarySlot("requester_demand", ComponentSide.WEST, 22),
    )
    return ConstrainedComponent.bounded(
        anchored,
        footprint,
        slots=slots,
        seams=(
            ComponentSeam(
                "mall",
                ComponentSide.WEST,
                ("recipe", "enable", "working", "requester_demand"),
            ),
        ),
    )


def _worker_component() -> ConstrainedComponent:
    return compose_component_seams(
        _worker_controller_component(),
        _mall_assembler_component(),
        left_seam="assembler",
        right_seam="mall",
        label="Deterministic mall worker cell",
    )


def _tail_component() -> ConstrainedComponent:
    """Terminate every lane explicitly and return blocked when a probe reaches the end."""

    footprint = ComponentFootprint(0.0, 0.0, _CONTROLLER_BODY_WIDTH, 8.0, _PITCH)
    docks = _bus_docks(
        side=ComponentSide.NORTH,
        seam_prefix="north_",
        port_prefix="",
        forward_out=False,
    )
    entities: list[dict[str, object]] = []
    anchors: list[BoundAnchor] = []
    ids: dict[str, int] = {}
    for entity_id, dock in enumerate(docks, start=1):
        position = footprint.boundary_position(dock.side, dock.slot)
        ids[dock.anchor] = entity_id
        entities.append(
            {
                "entity_number": entity_id,
                "name": "constant-combinator",
                "position": {"x": position[0], "y": position[1]},
                "player_description": f"MALL BUS TAIL {dock.anchor}",
            }
        )
        connector = 1 if dock.wire is WireColor.RED else 2
        anchors.append(BoundAnchor(dock.spec(), entity_id, connector, position))

    def arithmetic(
        entity_number: int,
        x: float,
        first: SignalId,
        multiplier: int,
        output: SignalId,
        description: str,
    ) -> dict[str, object]:
        return {
            "entity_number": entity_number,
            "name": "arithmetic-combinator",
            "position": {"x": x, "y": 3.0},
            "direction": 4,
            "player_description": description,
            "control_behavior": {
                "arithmetic_conditions": {
                    "operation": "*",
                    "first_signal": {"type": first.kind, "name": first.name},
                    "first_signal_networks": {"red": False, "green": True},
                    "second_constant": multiplier,
                    "output_signal": {"type": output.kind, "name": output.name},
                }
            },
        }

    blocked_id = 11
    accepted_id = 12
    busy_id = 13
    completion_id = 14
    recipe_sink_id = 15
    reserved_id = 16
    promised_id = 17
    entities.extend(
        (
            arithmetic(
                blocked_id,
                4.5,
                OFFER_VALID_SIGNAL,
                1,
                OFFER_BLOCKED_SIGNAL,
                "MALL BUS tail V -> B",
            ),
            arithmetic(
                accepted_id,
                9.0,
                OFFER_VALID_SIGNAL,
                0,
                OFFER_ACCEPTED_SIGNAL,
                "MALL BUS tail accepted zero",
            ),
            arithmetic(
                busy_id,
                12.0,
                OFFER_VALID_SIGNAL,
                0,
                BUSY_COUNT_SIGNAL,
                "MALL BUS tail busy zero",
            ),
            arithmetic(
                completion_id,
                15.0,
                OFFER_VALID_SIGNAL,
                0,
                COMPLETION_COUNT_SIGNAL,
                "MALL BUS tail completion zero",
            ),
            arithmetic(
                recipe_sink_id,
                18.0,
                _EACH,
                0,
                _EACH,
                "MALL BUS tail recipe sink",
            ),
            arithmetic(
                reserved_id,
                27.0,
                _EACH,
                0,
                _EACH,
                "MALL BUS tail reserved zero",
            ),
            arithmetic(
                promised_id,
                30.0,
                _EACH,
                0,
                _EACH,
                "MALL BUS tail promised zero",
            ),
        )
    )

    # Arithmetic input connector 2 carries GREEN; output connector 3 carries RED. The valid signal
    # fans through the three scalar-zero generators. Vector sinks/zero-generators remain on separate
    # GREEN input networks so the recipe, input, and product vectors never merge at the terminator.
    wires = [
        [ids["north_offer_valid"], 2, blocked_id, 2],
        [blocked_id, 3, ids["north_blocked"], 1],
        [ids["north_offer_valid"], 2, accepted_id, 2],
        [accepted_id, 3, ids["north_accepted"], 1],
        [accepted_id, 2, busy_id, 2],
        [busy_id, 3, ids["north_busy_count"], 1],
        [busy_id, 2, completion_id, 2],
        [completion_id, 3, ids["north_completion_count"], 1],
        [ids["north_offer_recipe"], 2, recipe_sink_id, 2],
        [ids["north_offer_inputs"], 2, reserved_id, 2],
        [reserved_id, 3, ids["north_reserved"], 1],
        [ids["north_offer_product"], 2, promised_id, 2],
        [promised_id, 3, ids["north_promised"], 1],
    ]
    blueprint: Blueprint = {
        "item": "blueprint",
        "label": "Deterministic mall bus tail",
        "version": FACTORIO_BLUEPRINT_VERSION,
        "entities": entities,
        "wires": wires,
    }
    anchored = AnchoredBlueprint(blueprint, tuple(anchors), "deterministic-mall-tail")
    return ConstrainedComponent.bounded(
        anchored,
        footprint,
        slots=(BoundarySlot(dock.anchor, dock.side, dock.slot) for dock in docks),
        seams=(
            ComponentSeam(
                "north_bus",
                ComponentSide.NORTH,
                tuple(dock.anchor for dock in docks),
            ),
        ),
    )


def build_seamed_worker_pool_component(worker_count: int = 2) -> ConstrainedComponent:
    """Build one bounded head + repeated worker cells + tail assembly."""

    if worker_count <= 0:
        raise ValueError("worker_count must be positive")

    current = _head_component()
    worker_template = _worker_component()
    for index in range(worker_count):
        current = compose_component_seams(
            current,
            worker_template,
            left_seam="south_bus",
            right_seam="north_bus",
            label=f"Deterministic mall — workers 0..{index}",
        )
    current = compose_component_seams(
        current,
        _tail_component(),
        left_seam="south_bus",
        right_seam="north_bus",
        label=f"Deterministic mall — {worker_count} seamed workers",
    )
    return current


def build_seamed_worker_pool_blueprint(worker_count: int = 2) -> Blueprint:
    component = build_seamed_worker_pool_component(worker_count)
    blueprint = component.anchored.blueprint
    blueprint["label"] = f"Deterministic mall — {worker_count} seamed anonymous workers"
    blueprint["icons"] = [{"signal": {"name": "assembling-machine-3"}, "index": 1}]
    return blueprint


def generate_seamed_worker_pool_blueprint_string(worker_count: int = 2) -> str:
    return encode_blueprint(build_seamed_worker_pool_blueprint(worker_count))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    print(generate_seamed_worker_pool_blueprint_string(args.workers))


if __name__ == "__main__":
    main()
