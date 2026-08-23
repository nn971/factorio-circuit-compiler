"""Closed-loop one-recipe mall probe targeting vanilla ``fast-splitter``.

This is intentionally smaller than the general autonomous-mall scheduler.  It composes one
feedback controller directly above the already in-game-validated seamed worker pool and proves the
first missing closed-loop property: a real logistic-network stock signal can cause one-craft jobs to
be issued repeatedly until a configured target is reached, without manually toggling ``offer_valid``.

The controller accepts one external vector called ``inventory``.  Wire a roboport configured to
read logistic-network contents to that dock, and put ``signal-T = desired fast-splitter count`` on
the same green network.  The recipe packet is fixed to the vanilla fast-splitter recipe:

* 1 splitter
* 10 iron gear wheels
* 10 electronic circuits
* -> 1 fast splitter

Only one worker is recommended for the first in-game acceptance run.  A new offer is issued only
when the pool is idle and the three ingredients are currently visible in the logistic network.
After acceptance, the controller snapshots the fast-splitter stock and refuses to issue another job
until the worker is idle *and* the roboport reports a larger fast-splitter count.  That settlement
barrier closes the short completion-to-provider visibility gap that would otherwise allow an extra
craft at the target boundary.

The first probe assumes no concurrent external consumption of fast splitters while a craft is
settling.  A general mall controller will replace this single-product snapshot barrier with an
explicit inventory/escrow accounting protocol.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from typing import Final

from factorio_circuit import SignalId
from factorio_circuit.devices import (
    ComponentSeam,
    ComponentSide,
    ConstrainedComponent,
    compose_component_seams,
)
from factorio_circuit.devices._blueprint import Blueprint, encode_blueprint
from factorio_circuit.devices.protocol import DevicePortDirection
from factorio_circuit.frontend import Circuit
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.ir.semantic import PayloadShape

from .seamed_worker_pool import (
    _Dock,
    _bus_docks,
    _compiled_component,
    _controller_footprint,
    _slot_x,
    build_seamed_worker_pool_component,
)
from .worker_pool import (
    BUSY_COUNT_SIGNAL,
    COMPLETION_COUNT_SIGNAL,
    OFFER_ACCEPTED_SIGNAL,
    OFFER_BLOCKED_SIGNAL,
    OFFER_VALID_SIGNAL,
)

FAST_SPLITTER: Final = SignalId("item", "fast-splitter")
SPLITTER: Final = SignalId("item", "splitter")
IRON_GEAR_WHEEL: Final = SignalId("item", "iron-gear-wheel")
ELECTRONIC_CIRCUIT: Final = SignalId("item", "electronic-circuit")
FAST_SPLITTER_RECIPE: Final = SignalId("recipe", "fast-splitter")
TARGET_SIGNAL: Final = SignalId("virtual", "signal-T")
SETTLING_SIGNAL: Final = SignalId("virtual", "signal-Z")

_VALID_STATE_SIGNAL: Final = SignalId("virtual", "signal-X")
_REARM_STATE_SIGNAL: Final = SignalId("virtual", "signal-Y")
_AWAIT_STOCK_STATE_SIGNAL: Final = SignalId("virtual", "signal-Z")

FAST_SPLITTER_INPUTS: Final = {
    SPLITTER: 1,
    IRON_GEAR_WHEEL: 10,
    ELECTRONIC_CIRCUIT: 10,
}
FAST_SPLITTER_PRODUCT: Final = {FAST_SPLITTER: 1}
FAST_SPLITTER_RECIPE_VECTOR: Final = {FAST_SPLITTER_RECIPE: 1}


def build_fast_splitter_controller() -> Circuit:
    """Build the one-recipe stock-feedback controller above the worker-pool four-phase ABI."""

    circuit = Circuit("fast_splitter_closed_loop_controller")

    inventory = circuit.signals("inventory")
    blocked = circuit.input("pool_blocked") != 0
    accepted = circuit.input("pool_accepted") != 0
    busy_count = circuit.input("pool_busy_count")
    completion_count = circuit.input("pool_completion_count")
    reserved = circuit.signals("pool_reserved")
    promised = circuit.signals("pool_promised")

    valid_reg = circuit.freeze("controller_offer_valid")
    rearm_reg = circuit.freeze("controller_rearm_low")
    await_stock_reg = circuit.freeze("controller_await_stock")
    baseline_reg = circuit.freeze("controller_stock_baseline")

    valid = valid_reg.sample().signal(_VALID_STATE_SIGNAL) != 0
    rearm_low = rearm_reg.sample().signal(_REARM_STATE_SIGNAL) != 0
    await_stock = await_stock_reg.sample().signal(_AWAIT_STOCK_STATE_SIGNAL) != 0
    baseline_fast_splitters = baseline_reg.sample().signal(FAST_SPLITTER)

    target = inventory.signal(TARGET_SIGNAL)
    fast_splitter_stock = inventory.signal(FAST_SPLITTER)
    fast_splitter_promised = promised.signal(FAST_SPLITTER)
    idle = (busy_count != 0).logical_not()

    ingredients_ready = (
        (inventory.signal(SPLITTER) >= 1)
        * (inventory.signal(IRON_GEAR_WHEEL) >= 10)
        * (inventory.signal(ELECTRONIC_CIRCUIT) >= 10)
    )
    deficit = fast_splitter_stock + fast_splitter_promised < target
    ready = (
        valid.logical_not()
        * rearm_low.logical_not()
        * await_stock.logical_not()
        * idle
    )
    issue = ready * deficit * ingredients_ready

    # Four-phase producer state.  Once accepted, V drops and stays low until both response levels
    # have returned low at this controller.  Observing that round-trip low phase is stronger than a
    # fixed delay and prevents the HEAD ``seen`` latch from missing the next envelope.
    valid_reg.set(
        circuit.constant_signals({_VALID_STATE_SIGNAL: 1}).gate(issue),
        when=issue | accepted,
    )
    clear_rearm = rearm_low * accepted.logical_not() * blocked.logical_not()
    rearm_reg.set(
        circuit.constant_signals({_REARM_STATE_SIGNAL: 1}).gate(accepted),
        when=accepted | clear_rearm,
    )

    # Capture physical stock at acceptance and keep a completion credit until the product is
    # actually visible through the roboport.  The worker's promise clears when assembler working
    # returns low, which can precede output-inserter/provider visibility by a few ticks.
    output_visible = (
        await_stock
        * idle
        * (fast_splitter_stock > baseline_fast_splitters)
    )
    await_stock_reg.set(
        circuit.constant_signals({_AWAIT_STOCK_STATE_SIGNAL: 1}).gate(accepted),
        when=accepted | output_visible,
    )
    baseline_reg.set(inventory.gate(accepted), when=accepted)

    # The packet is immutable and is therefore safe to publish continuously while V is low.  HEAD
    # still gates the packet with its received V and retains its own packet-before-probe delay.
    circuit.output("pool_offer_valid", valid)
    circuit.output("pool_offer_recipe", circuit.constant_signals(FAST_SPLITTER_RECIPE_VECTOR))
    circuit.output("pool_offer_inputs", circuit.constant_signals(FAST_SPLITTER_INPUTS))
    circuit.output("pool_offer_product", circuit.constant_signals(FAST_SPLITTER_PRODUCT))

    # External diagnostics are deliberately passive copies of the validated worker-pool ABI.
    circuit.output("diag_offer_valid", valid)
    circuit.output("diag_blocked", blocked)
    circuit.output("diag_accepted", accepted)
    circuit.output("diag_busy_count", busy_count)
    circuit.output("diag_completion_count", completion_count)
    circuit.output("diag_reserved", reserved)
    circuit.output("diag_promised", promised)
    circuit.output("diag_settling", await_stock)
    return circuit


def _external_docks() -> tuple[_Dock, ...]:
    return (
        _Dock(
            "inventory",
            "inventory",
            ComponentSide.NORTH,
            _slot_x(0),
            DevicePortDirection.INPUT,
            PayloadShape.VECTOR,
            WireColor.GREEN,
        ),
        _Dock(
            "diag_offer_valid",
            "offer_valid",
            ComponentSide.NORTH,
            _slot_x(1),
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            WireColor.GREEN,
            OFFER_VALID_SIGNAL,
        ),
        _Dock(
            "diag_blocked",
            "blocked",
            ComponentSide.NORTH,
            _slot_x(2),
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            WireColor.RED,
            OFFER_BLOCKED_SIGNAL,
        ),
        _Dock(
            "diag_accepted",
            "accepted",
            ComponentSide.NORTH,
            _slot_x(3),
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            WireColor.RED,
            OFFER_ACCEPTED_SIGNAL,
        ),
        _Dock(
            "diag_busy_count",
            "busy_count",
            ComponentSide.NORTH,
            _slot_x(4),
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            WireColor.RED,
            BUSY_COUNT_SIGNAL,
        ),
        _Dock(
            "diag_completion_count",
            "completion_count",
            ComponentSide.NORTH,
            _slot_x(5),
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            WireColor.RED,
            COMPLETION_COUNT_SIGNAL,
        ),
        _Dock(
            "diag_reserved",
            "reserved",
            ComponentSide.NORTH,
            _slot_x(6),
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            WireColor.RED,
        ),
        _Dock(
            "diag_promised",
            "promised",
            ComponentSide.NORTH,
            _slot_x(7),
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            WireColor.RED,
        ),
        _Dock(
            "diag_settling",
            "settling",
            ComponentSide.NORTH,
            _slot_x(8),
            DevicePortDirection.OUTPUT,
            PayloadShape.SCALAR,
            WireColor.GREEN,
            SETTLING_SIGNAL,
        ),
    )


def _controller_component() -> ConstrainedComponent:
    footprint = _controller_footprint()
    external = _external_docks()
    south = _bus_docks(
        side=ComponentSide.SOUTH,
        seam_prefix="south_",
        port_prefix="pool_",
        forward_out=True,
    )
    return _compiled_component(
        build_fast_splitter_controller(),
        footprint,
        external + south,
        (
            ComponentSeam(
                "control",
                ComponentSide.NORTH,
                tuple(dock.anchor for dock in external),
            ),
            ComponentSeam(
                "south_bus",
                ComponentSide.SOUTH,
                tuple(dock.anchor for dock in south),
            ),
        ),
        label="Fast splitter closed-loop controller",
    )


def build_fast_splitter_probe_component(worker_count: int = 1) -> ConstrainedComponent:
    """Compose the stock-feedback controller with the validated seamed worker pool."""

    if worker_count <= 0:
        raise ValueError("worker_count must be positive")
    return compose_component_seams(
        _controller_component(),
        build_seamed_worker_pool_component(worker_count),
        left_seam="south_bus",
        right_seam="external",
        label=f"Fast splitter closed-loop mall probe — {worker_count} worker(s)",
    )


def build_fast_splitter_probe_blueprint(worker_count: int = 1) -> Blueprint:
    component = build_fast_splitter_probe_component(worker_count)
    blueprint = deepcopy(component.anchored.blueprint)
    blueprint["label"] = f"Fast splitter closed-loop mall probe — {worker_count} worker(s)"
    blueprint["icons"] = [{"signal": {"name": "fast-splitter"}, "index": 1}]
    return blueprint


def generate_fast_splitter_probe_blueprint_string(worker_count: int = 1) -> str:
    return encode_blueprint(build_fast_splitter_probe_blueprint(worker_count))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    print(generate_fast_splitter_probe_blueprint_string(args.workers))


if __name__ == "__main__":
    main()
