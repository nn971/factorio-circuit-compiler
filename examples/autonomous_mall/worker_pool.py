"""Circuit-facing anonymous worker pool for the deterministic autonomous-mall prototype.

The offline scheduler in :mod:`examples.autonomous_mall.scheduler` decides *which* deterministic
crafts are useful. This module implements the next boundary down: accepting one already-formed
craft job and executing it on the first idle assembler worker while publishing additive reservation
and promised-output ledgers.

The first physical protocol deliberately uses **one craft per accepted job**. ``AssemblerDevice``
exposes requester demand as a steady-state logistic setpoint, so holding an entire multi-craft batch
as the setpoint while the assembler consumes items would invite repeated replenishment. A later
batch protocol may preload/escrow a batch explicitly; this prototype keeps the exact execution rule
small and fail-safe.

Job envelope (all Level inputs)
-------------------------------

``offer_valid``
    Nonzero while one job envelope is being offered. The producer must drop this to zero between
    distinct envelopes. The pool contains a ``seen`` latch, so holding one envelope high until
    ``offer_accepted`` is safe and cannot duplicate the job.

``offer_recipe``
    Whole-vector recipe selector, normally the one item/recipe signal expected by Factorio's
    Set-recipe mode.

``offer_inputs``
    Exact item quantities reserved for one craft.

``offer_product``
    Exact deterministic product vector promised by that craft.

For each worker ``i`` the pool consumes ``worker_i_working`` and emits the three command ports used
by :class:`factorio_circuit.devices.AssemblerDevice`:

``worker_i_recipe`` / ``worker_i_enable`` / ``worker_i_requester_demand``.

A worker has three whole-vector registers: held recipe, held reservation, and held promise. A
nonempty promise means busy. A nonempty recipe means the worker is still waiting for the assembler
to report ``working=1``. On that observation the recipe is withdrawn; Factorio's validated
Set-recipe behavior lets the already-started craft finish while preventing another craft from
starting. The promise and reservation remain held until ``working`` returns to zero.

The aggregate ``reserved`` and ``promised`` outputs are additive buses. They include a newly claimed
job combinationally during the claim reaction and then continuously from worker state, avoiding a
one-reaction accounting hole at dispatch.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Final

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.devices import (
    ASSEMBLER_ENABLE_SIGNAL,
    ASSEMBLER_WORKING_SIGNAL,
    AnchorBinding,
    AnchoredBlueprint,
    AnchorSpec,
    AssemblerDevice,
    BoundAnchor,
    CompiledAnchorBinding,
    compiled_module_as_anchored_blueprint,
    compose_anchored_blueprints,
    device_as_anchored_blueprint,
)
from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.frontend import Circuit
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape, TemporalModality
from factorio_circuit.synthesis.safe_folded_crossbar import safe_folded_crossbar_options

OFFER_VALID_SIGNAL: Final = SignalId("virtual", "signal-V")
OFFER_ACCEPTED_SIGNAL: Final = SignalId("virtual", "signal-A")
OFFER_BLOCKED_SIGNAL: Final = SignalId("virtual", "signal-B")
BUSY_COUNT_SIGNAL: Final = SignalId("virtual", "signal-C")
COMPLETION_COUNT_SIGNAL: Final = SignalId("virtual", "signal-D")
_OFFER_SEEN_SIGNAL: Final = SignalId("virtual", "signal-S")


@dataclass(frozen=True, slots=True)
class WorkerPorts:
    """Stable compiler-port names for one logical worker."""

    index: int

    @property
    def working(self) -> str:
        return f"worker_{self.index}_working"

    @property
    def recipe(self) -> str:
        return f"worker_{self.index}_recipe"

    @property
    def enable(self) -> str:
        return f"worker_{self.index}_enable"

    @property
    def requester_demand(self) -> str:
        return f"worker_{self.index}_requester_demand"

    @property
    def reservation(self) -> str:
        return f"worker_{self.index}_reservation"

    @property
    def promise(self) -> str:
        return f"worker_{self.index}_promise"

    @property
    def claim(self) -> str:
        return f"worker_{self.index}_claim"

    @property
    def busy(self) -> str:
        return f"worker_{self.index}_busy"

    @property
    def finished(self) -> str:
        return f"worker_{self.index}_finished"


def worker_ports(index: int) -> WorkerPorts:
    if index < 0:
        raise ValueError("worker index must be non-negative")
    return WorkerPorts(index)


def build_worker_pool(worker_count: int = 2) -> Circuit:
    """Build the deterministic first-free worker-pool controller.

    The offer handshake is four-phase: a producer may hold ``offer_valid`` high until
    ``offer_accepted``; it must then lower valid before presenting another envelope. If all workers
    are busy, ``offer_blocked`` remains high and the still-unseen envelope is claimed automatically
    when a worker becomes idle.
    """

    if worker_count <= 0:
        raise ValueError("worker_count must be positive")

    circuit = Circuit(f"deterministic_mall_worker_pool_{worker_count}")
    offer_valid = circuit.input("offer_valid") != 0
    offer_recipe = circuit.signals("offer_recipe")
    offer_inputs = circuit.signals("offer_inputs")
    offer_product = circuit.signals("offer_product")
    empty = circuit.constant_signals({})
    scalar_zero = offer_valid * 0

    seen_reg = circuit.freeze("offer_seen")
    seen = seen_reg.sample().signal(_OFFER_SEEN_SIGNAL) != 0
    well_formed = offer_recipe.any() * offer_product.any()
    remaining_offer = offer_valid * seen.logical_not() * well_formed

    total_reservation = empty
    total_promise = empty
    accepted = scalar_zero
    completion_count = scalar_zero
    busy_count = scalar_zero

    for index in range(worker_count):
        ports = worker_ports(index)
        working = circuit.input(ports.working) != 0

        recipe_reg = circuit.freeze(f"worker_{index}_recipe_state")
        reservation_reg = circuit.freeze(f"worker_{index}_reservation_state")
        promise_reg = circuit.freeze(f"worker_{index}_promise_state")

        held_recipe = recipe_reg.sample()
        held_reservation = reservation_reg.sample()
        held_promise = promise_reg.sample()

        busy = held_promise.any()
        idle = busy.logical_not()
        starting = busy * held_recipe.any()
        waiting = busy * held_recipe.any().logical_not()

        claim = remaining_offer * idle
        started = starting * working
        finished = waiting * working.logical_not()

        # A busy worker forwards the still-unclaimed envelope; the first idle worker consumes it.
        remaining_offer = remaining_offer * busy

        # Each register has one mutation site. Claim and the corresponding release transition are
        # mutually exclusive because claim requires idle while started/finished require busy.
        recipe_reg.set(offer_recipe.gate(claim), when=claim | started)
        reservation_reg.set(offer_inputs.gate(claim), when=claim | finished)
        promise_reg.set(offer_product.gate(claim), when=claim | finished)

        # Include a just-claimed envelope immediately on the public ledgers; next reaction the same
        # values come from held state, so there is no accounting gap at the handoff.
        live_reservation = held_reservation + offer_inputs.gate(claim)
        live_promise = held_promise + offer_product.gate(claim)
        live_busy = busy | claim
        total_reservation = total_reservation + live_reservation
        total_promise = total_promise + live_promise
        accepted = accepted + claim
        completion_count = completion_count + finished
        busy_count = busy_count + live_busy

        # Keep enable asserted through the current craft. Recipe/request demand are present only
        # while waiting for working=1; withdrawing recipe at that point prevents a second craft.
        circuit.output(ports.recipe, held_recipe.gate(starting))
        circuit.output(ports.enable, busy)
        circuit.output(ports.requester_demand, held_reservation.gate(starting))
        circuit.output(ports.reservation, live_reservation)
        circuit.output(ports.promise, live_promise)
        circuit.output(ports.claim, claim)
        circuit.output(ports.busy, live_busy)
        circuit.output(ports.finished, finished)

    # Remember an accepted envelope until its producer has visibly dropped valid. A blocked offer
    # leaves seen=0, so it remains eligible for the first worker that later becomes idle.
    accepted_any = accepted != 0
    clear_seen = seen * offer_valid.logical_not()
    seen_change = accepted_any | clear_seen
    seen_value = circuit.constant_signals({_OFFER_SEEN_SIGNAL: 1}).gate(accepted_any)
    seen_reg.set(seen_value, when=seen_change)

    circuit.output("reserved", total_reservation)
    circuit.output("promised", total_promise)
    circuit.output("offer_accepted", accepted)
    circuit.output("offer_blocked", remaining_offer)
    circuit.output("busy_count", busy_count)
    circuit.output("completion_count", completion_count)
    return circuit


def _compile_worker_pool(worker_count: int):
    """Compile a probe with the deterministic search-free layout policy."""

    return compile_circuit(
        build_worker_pool(worker_count),
        placement=safe_folded_crossbar_options(),
    )


def _blueprint_bounds(wrapper: dict[str, object]) -> tuple[float, float, float, float]:
    blueprint = wrapper.get("blueprint")
    if not isinstance(blueprint, dict):
        raise ValueError("compiled result has no blueprint object")
    entities = blueprint.get("entities", [])
    if not isinstance(entities, list) or not entities:
        return (0.0, 0.0, 0.0, 0.0)
    points: list[tuple[float, float]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        position = entity.get("position")
        if not isinstance(position, dict):
            continue
        points.append((float(position["x"]), float(position["y"])))
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _renamed_device(component: AnchoredBlueprint, prefix: str) -> AnchoredBlueprint:
    anchors = tuple(
        BoundAnchor(
            AnchorSpec(
                f"{prefix}_{anchor.name}",
                anchor.spec.direction,
                anchor.spec.payload_shape,
                anchor.spec.modality,
                anchor.spec.wire,
                anchor.spec.signal,
                anchor.spec.required,
            ),
            anchor.entity_number,
            anchor.connector_id,
            anchor.position,
        )
        for anchor in component.anchors
    )
    return AnchoredBlueprint(component.blueprint, anchors, f"{prefix}-{component.label}")


def _level_anchor(
    name: str,
    direction: DevicePortDirection,
    shape: PayloadShape,
    wire: WireColor,
    signal: SignalId | None = None,
) -> AnchorSpec:
    return AnchorSpec(name, direction, shape, TemporalModality.LEVEL, wire, signal)


def _device_route(
    device_port: str,
    offset: tuple[float, float],
    anchor_position: tuple[float, float],
) -> tuple[tuple[float, float], ...]:
    """Approach device docks from outside the worker footprint.

    Command docks are on the left edge. The ``working`` dock is on the right, so its route goes
    below the worker and comes back from the right instead of dropping relay combinators through the
    assembler/requester/provider footprint.
    """

    left_clearance = offset[0] - 4.0
    if device_port != "working":
        return ((left_clearance, anchor_position[1]),)
    bottom_clearance = offset[1] + 21.5
    right_clearance = offset[0] + 24.0
    return (
        (left_clearance, bottom_clearance),
        (right_clearance, bottom_clearance),
        (right_clearance, anchor_position[1]),
    )


def build_worker_pool_probe_blueprint(worker_count: int = 2) -> Blueprint:
    """Compile the pool and attach ``worker_count`` real ``AssemblerDevice`` instances.

    The resulting blueprint intentionally leaves the offer envelope and aggregate-ledger anchors
    exposed for manual wiring. Unused per-device observation anchors (ingredients, contents,
    finished) also survive with worker-prefixed names, which makes the prototype convenient to
    inspect in game without modifying device internals.
    """

    result = _compile_worker_pool(worker_count)
    min_x, min_y, max_x, _max_y = _blueprint_bounds(result.blueprint_json)

    device_template = device_as_anchored_blueprint(
        AssemblerDevice(label="Deterministic mall worker").build(),
        label="assembler-worker",
    )
    device_offsets = [(max_x + 32.0, min_y + index * 24.0) for index in range(worker_count)]

    bindings: list[CompiledAnchorBinding] = []

    # External job/ledger ABI on the left of the compiled controller.
    external_x = min_x - 16.0
    external_specs = (
        (
            "offer_valid",
            _level_anchor(
                "offer_valid",
                DevicePortDirection.INPUT,
                PayloadShape.SCALAR,
                WireColor.GREEN,
                OFFER_VALID_SIGNAL,
            ),
        ),
        (
            "offer_recipe",
            _level_anchor(
                "offer_recipe",
                DevicePortDirection.INPUT,
                PayloadShape.VECTOR,
                WireColor.GREEN,
            ),
        ),
        (
            "offer_inputs",
            _level_anchor(
                "offer_inputs",
                DevicePortDirection.INPUT,
                PayloadShape.VECTOR,
                WireColor.GREEN,
            ),
        ),
        (
            "offer_product",
            _level_anchor(
                "offer_product",
                DevicePortDirection.INPUT,
                PayloadShape.VECTOR,
                WireColor.GREEN,
            ),
        ),
        (
            "reserved",
            _level_anchor(
                "reserved",
                DevicePortDirection.OUTPUT,
                PayloadShape.VECTOR,
                WireColor.RED,
            ),
        ),
        (
            "promised",
            _level_anchor(
                "promised",
                DevicePortDirection.OUTPUT,
                PayloadShape.VECTOR,
                WireColor.RED,
            ),
        ),
        (
            "offer_accepted",
            _level_anchor(
                "offer_accepted",
                DevicePortDirection.OUTPUT,
                PayloadShape.SCALAR,
                WireColor.RED,
                OFFER_ACCEPTED_SIGNAL,
            ),
        ),
        (
            "offer_blocked",
            _level_anchor(
                "offer_blocked",
                DevicePortDirection.OUTPUT,
                PayloadShape.SCALAR,
                WireColor.RED,
                OFFER_BLOCKED_SIGNAL,
            ),
        ),
        (
            "busy_count",
            _level_anchor(
                "busy_count",
                DevicePortDirection.OUTPUT,
                PayloadShape.SCALAR,
                WireColor.RED,
                BUSY_COUNT_SIGNAL,
            ),
        ),
        (
            "completion_count",
            _level_anchor(
                "completion_count",
                DevicePortDirection.OUTPUT,
                PayloadShape.SCALAR,
                WireColor.RED,
                COMPLETION_COUNT_SIGNAL,
            ),
        ),
    )
    for slot, (port, spec) in enumerate(external_specs):
        bindings.append(CompiledAnchorBinding(port, spec, (external_x, min_y + slot * 3.0)))

    # Bind only the four ports needed by the one-craft protocol. The device's other observation
    # ports remain visible under worker-prefixed names after composition.
    for index, offset in enumerate(device_offsets):
        ports = worker_ports(index)
        device_bindings = (
            (
                ports.recipe,
                "recipe",
                DevicePortDirection.OUTPUT,
                PayloadShape.VECTOR,
                WireColor.GREEN,
                None,
            ),
            (
                ports.enable,
                "enable",
                DevicePortDirection.OUTPUT,
                PayloadShape.SCALAR,
                WireColor.GREEN,
                ASSEMBLER_ENABLE_SIGNAL,
            ),
            (
                ports.requester_demand,
                "requester_demand",
                DevicePortDirection.OUTPUT,
                PayloadShape.VECTOR,
                WireColor.GREEN,
                None,
            ),
            (
                ports.working,
                "working",
                DevicePortDirection.INPUT,
                PayloadShape.SCALAR,
                WireColor.RED,
                ASSEMBLER_WORKING_SIGNAL,
            ),
        )
        for controller_port, device_port, direction, shape, wire, signal in device_bindings:
            local = device_template.anchor(device_port)
            position = (local.position[0] + offset[0], local.position[1] + offset[1])
            bindings.append(
                CompiledAnchorBinding(
                    controller_port,
                    _level_anchor(
                        f"controller_worker_{index}_{device_port}",
                        direction,
                        shape,
                        wire,
                        signal,
                    ),
                    position,
                    route=_device_route(device_port, offset, position),
                )
            )

    controller = compiled_module_as_anchored_blueprint(
        result,
        bindings,
        label=f"Deterministic mall worker pool controller ({worker_count})",
    )

    current = controller
    for index, offset in enumerate(device_offsets):
        prefix = f"worker_{index}"
        renamed = _renamed_device(device_template, prefix)
        composed = compose_anchored_blueprints(
            current,
            renamed,
            bindings=(
                AnchorBinding(f"controller_worker_{index}_recipe", f"{prefix}_recipe"),
                AnchorBinding(f"controller_worker_{index}_enable", f"{prefix}_enable"),
                AnchorBinding(
                    f"controller_worker_{index}_requester_demand",
                    f"{prefix}_requester_demand",
                ),
                AnchorBinding(f"controller_worker_{index}_working", f"{prefix}_working"),
            ),
            right_offset=offset,
            label=f"Deterministic mall worker pool ({worker_count})",
        )
        current = AnchoredBlueprint(
            composed.blueprint,
            composed.anchors,
            f"deterministic-mall-worker-pool-{worker_count}",
        )

    blueprint = current.blueprint
    blueprint["label"] = f"Deterministic mall — {worker_count} anonymous workers"
    blueprint["icons"] = [{"signal": {"name": "assembling-machine-3"}, "index": 1}]
    return blueprint


def generate_worker_pool_probe_blueprint_string(worker_count: int = 2) -> str:
    return encode_blueprint(build_worker_pool_probe_blueprint(worker_count))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--controller-only",
        action="store_true",
        help="emit only the compiled controller, without AssemblerDevice workers",
    )
    args = parser.parse_args()

    if args.controller_only:
        print(_compile_worker_pool(args.workers).blueprint_string)
    else:
        print(generate_worker_pool_probe_blueprint_string(args.workers))


if __name__ == "__main__":
    main()
