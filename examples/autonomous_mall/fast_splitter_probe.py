"""Closed-loop recursive mall probe targeting vanilla ``fast-splitter``.

This probe composes one stock-feedback controller directly above the already in-game-validated
seamed worker pool.  The only prescribed source materials are iron plates and copper plates.  The
controller recursively manufactures the ordinary vanilla intermediates needed for one fast splitter
at a time, then repeats until the configured final-stock target is reached.

Wire a roboport configured to read logistic-network contents to the external ``inventory`` dock and
put ``signal-T = desired fast-splitter count`` on the same green network.  With no useful
intermediates initially stocked, the controller may issue these recipes:

* copper-cable: 1 copper plate -> 2 copper cable;
* electronic-circuit: 3 copper cable + 1 iron plate -> 1 electronic circuit;
* iron-gear-wheel: 2 iron plates -> 1 iron gear wheel;
* transport-belt: 1 iron plate + 1 iron gear wheel -> 2 transport belts;
* splitter: 4 transport belts + 5 electronic circuits + 5 iron plates -> 1 splitter;
* fast-splitter: 1 splitter + 10 iron gear wheels + 10 electronic circuits -> 1 fast splitter.

One worker is recommended for the first in-game acceptance run.  The controller issues only one
craft at a time.  After acceptance it snapshots the product stock exactly once and refuses to issue
the next job until the worker is idle and the roboport reports that product stock has increased.
This settlement barrier closes the completion-to-provider visibility gap without assuming that the
pool's accepted response is a one-tick pulse.

The probe assumes no concurrent external consumption of the intermediate/final products while a
craft is settling.  It is deliberately a target-specific recursive scheduler; the next milestone is
to replace the hard-coded dependency decisions with the general vector ROM/scheduler design.
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
TRANSPORT_BELT: Final = SignalId("item", "transport-belt")
IRON_GEAR_WHEEL: Final = SignalId("item", "iron-gear-wheel")
ELECTRONIC_CIRCUIT: Final = SignalId("item", "electronic-circuit")
COPPER_CABLE: Final = SignalId("item", "copper-cable")
IRON_PLATE: Final = SignalId("item", "iron-plate")
COPPER_PLATE: Final = SignalId("item", "copper-plate")

FAST_SPLITTER_RECIPE: Final = SignalId("recipe", "fast-splitter")
SPLITTER_RECIPE: Final = SignalId("recipe", "splitter")
TRANSPORT_BELT_RECIPE: Final = SignalId("recipe", "transport-belt")
IRON_GEAR_WHEEL_RECIPE: Final = SignalId("recipe", "iron-gear-wheel")
ELECTRONIC_CIRCUIT_RECIPE: Final = SignalId("recipe", "electronic-circuit")
COPPER_CABLE_RECIPE: Final = SignalId("recipe", "copper-cable")

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

_SPLITTER_INPUTS: Final = {
    TRANSPORT_BELT: 4,
    ELECTRONIC_CIRCUIT: 5,
    IRON_PLATE: 5,
}
_SPLITTER_PRODUCT: Final = {SPLITTER: 1}
_SPLITTER_RECIPE_VECTOR: Final = {SPLITTER_RECIPE: 1}

_TRANSPORT_BELT_INPUTS: Final = {IRON_PLATE: 1, IRON_GEAR_WHEEL: 1}
_TRANSPORT_BELT_PRODUCT: Final = {TRANSPORT_BELT: 2}
_TRANSPORT_BELT_RECIPE_VECTOR: Final = {TRANSPORT_BELT_RECIPE: 1}

_IRON_GEAR_WHEEL_INPUTS: Final = {IRON_PLATE: 2}
_IRON_GEAR_WHEEL_PRODUCT: Final = {IRON_GEAR_WHEEL: 1}
_IRON_GEAR_WHEEL_RECIPE_VECTOR: Final = {IRON_GEAR_WHEEL_RECIPE: 1}

_ELECTRONIC_CIRCUIT_INPUTS: Final = {COPPER_CABLE: 3, IRON_PLATE: 1}
_ELECTRONIC_CIRCUIT_PRODUCT: Final = {ELECTRONIC_CIRCUIT: 1}
_ELECTRONIC_CIRCUIT_RECIPE_VECTOR: Final = {ELECTRONIC_CIRCUIT_RECIPE: 1}

_COPPER_CABLE_INPUTS: Final = {COPPER_PLATE: 1}
_COPPER_CABLE_PRODUCT: Final = {COPPER_CABLE: 2}
_COPPER_CABLE_RECIPE_VECTOR: Final = {COPPER_CABLE_RECIPE: 1}

_SETTLED_PRODUCTS: Final = (
    FAST_SPLITTER,
    SPLITTER,
    TRANSPORT_BELT,
    IRON_GEAR_WHEEL,
    ELECTRONIC_CIRCUIT,
    COPPER_CABLE,
)


def _choice_vectors(circuit: Circuit, choices):
    recipe = circuit.constant_signals({})
    inputs = circuit.constant_signals({})
    product = circuit.constant_signals({})
    issue = None
    for selected, recipe_vector, input_vector, product_vector in choices:
        recipe = recipe + circuit.constant_signals(recipe_vector).gate(selected)
        inputs = inputs + circuit.constant_signals(input_vector).gate(selected)
        product = product + circuit.constant_signals(product_vector).gate(selected)
        issue = selected if issue is None else issue | selected
    assert issue is not None
    return issue, recipe, inputs, product


def build_fast_splitter_controller() -> Circuit:
    """Build a one-worker recursive stock-feedback controller from iron/copper plates."""

    circuit = Circuit("fast_splitter_raw_plate_closed_loop_controller")

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
    settle_product_reg = circuit.freeze("controller_settle_product")
    job_recipe_reg = circuit.freeze("controller_job_recipe")
    job_inputs_reg = circuit.freeze("controller_job_inputs")
    job_product_reg = circuit.freeze("controller_job_product")

    valid = valid_reg.sample().signal(_VALID_STATE_SIGNAL) != 0
    rearm_low = rearm_reg.sample().signal(_REARM_STATE_SIGNAL) != 0
    await_stock = await_stock_reg.sample().signal(_AWAIT_STOCK_STATE_SIGNAL) != 0
    baseline = baseline_reg.sample()
    settle_product = settle_product_reg.sample()
    held_recipe = job_recipe_reg.sample()
    held_inputs = job_inputs_reg.sample()
    held_product = job_product_reg.sample()

    target = inventory.signal(TARGET_SIGNAL)
    fast_splitter_stock = inventory.signal(FAST_SPLITTER)
    idle = (busy_count != 0).logical_not()
    final_deficit = fast_splitter_stock < target

    ready = (
        valid.logical_not()
        * rearm_low.logical_not()
        * await_stock.logical_not()
        * idle
        * final_deficit
    )

    splitter_missing = inventory.signal(SPLITTER) < 1
    splitter_ready = splitter_missing.logical_not()

    # While a splitter is missing, reserve enough circuits for both the splitter (5) and the final
    # fast splitter (10).  Belts are then filled to four, recursively producing their two required
    # gear crafts.  Once the splitter exists, only the final ten gears/circuits remain relevant.
    need_splitter_circuit = splitter_missing * (inventory.signal(ELECTRONIC_CIRCUIT) < 15)
    can_make_circuit = (
        (inventory.signal(COPPER_CABLE) >= 3) * (inventory.signal(IRON_PLATE) >= 1)
    )
    choose_splitter_circuit = ready * need_splitter_circuit * can_make_circuit
    choose_splitter_cable = (
        ready
        * need_splitter_circuit
        * (inventory.signal(COPPER_CABLE) < 3)
        * (inventory.signal(COPPER_PLATE) >= 1)
    )

    splitter_circuits_ready = inventory.signal(ELECTRONIC_CIRCUIT) >= 15
    need_belt = splitter_missing * splitter_circuits_ready * (inventory.signal(TRANSPORT_BELT) < 4)
    can_make_belt = (
        (inventory.signal(IRON_GEAR_WHEEL) >= 1) * (inventory.signal(IRON_PLATE) >= 1)
    )
    choose_belt = ready * need_belt * can_make_belt
    choose_belt_gear = (
        ready
        * need_belt
        * (inventory.signal(IRON_GEAR_WHEEL) < 1)
        * (inventory.signal(IRON_PLATE) >= 2)
    )

    choose_splitter = (
        ready
        * splitter_missing
        * splitter_circuits_ready
        * (inventory.signal(TRANSPORT_BELT) >= 4)
        * (inventory.signal(IRON_PLATE) >= 5)
    )

    need_final_gear = splitter_ready * (inventory.signal(IRON_GEAR_WHEEL) < 10)
    choose_final_gear = ready * need_final_gear * (inventory.signal(IRON_PLATE) >= 2)

    final_gears_ready = inventory.signal(IRON_GEAR_WHEEL) >= 10
    need_final_circuit = (
        splitter_ready * final_gears_ready * (inventory.signal(ELECTRONIC_CIRCUIT) < 10)
    )
    choose_final_circuit = ready * need_final_circuit * can_make_circuit
    choose_final_cable = (
        ready
        * need_final_circuit
        * (inventory.signal(COPPER_CABLE) < 3)
        * (inventory.signal(COPPER_PLATE) >= 1)
    )

    choose_fast_splitter = (
        ready
        * splitter_ready
        * final_gears_ready
        * (inventory.signal(ELECTRONIC_CIRCUIT) >= 10)
    )

    issue, chosen_recipe, chosen_inputs, chosen_product = _choice_vectors(
        circuit,
        (
            (
                choose_splitter_circuit,
                _ELECTRONIC_CIRCUIT_RECIPE_VECTOR,
                _ELECTRONIC_CIRCUIT_INPUTS,
                _ELECTRONIC_CIRCUIT_PRODUCT,
            ),
            (
                choose_splitter_cable,
                _COPPER_CABLE_RECIPE_VECTOR,
                _COPPER_CABLE_INPUTS,
                _COPPER_CABLE_PRODUCT,
            ),
            (
                choose_belt,
                _TRANSPORT_BELT_RECIPE_VECTOR,
                _TRANSPORT_BELT_INPUTS,
                _TRANSPORT_BELT_PRODUCT,
            ),
            (
                choose_belt_gear,
                _IRON_GEAR_WHEEL_RECIPE_VECTOR,
                _IRON_GEAR_WHEEL_INPUTS,
                _IRON_GEAR_WHEEL_PRODUCT,
            ),
            (
                choose_splitter,
                _SPLITTER_RECIPE_VECTOR,
                _SPLITTER_INPUTS,
                _SPLITTER_PRODUCT,
            ),
            (
                choose_final_gear,
                _IRON_GEAR_WHEEL_RECIPE_VECTOR,
                _IRON_GEAR_WHEEL_INPUTS,
                _IRON_GEAR_WHEEL_PRODUCT,
            ),
            (
                choose_final_circuit,
                _ELECTRONIC_CIRCUIT_RECIPE_VECTOR,
                _ELECTRONIC_CIRCUIT_INPUTS,
                _ELECTRONIC_CIRCUIT_PRODUCT,
            ),
            (
                choose_final_cable,
                _COPPER_CABLE_RECIPE_VECTOR,
                _COPPER_CABLE_INPUTS,
                _COPPER_CABLE_PRODUCT,
            ),
            (
                choose_fast_splitter,
                FAST_SPLITTER_RECIPE_VECTOR,
                FAST_SPLITTER_INPUTS,
                FAST_SPLITTER_PRODUCT,
            ),
        ),
    )

    # Latch the selected packet before V becomes visible.  All packet fields are held unchanged
    # through the complete four-phase transaction, so a changing roboport inventory cannot mutate
    # an offer that HEAD has already started to transport.
    job_recipe_reg.set(chosen_recipe.gate(issue), when=issue)
    job_inputs_reg.set(chosen_inputs.gate(issue), when=issue)
    job_product_reg.set(chosen_product.gate(issue), when=issue)

    valid_reg.set(
        circuit.constant_signals({_VALID_STATE_SIGNAL: 1}).gate(issue),
        when=issue | accepted,
    )
    clear_rearm = rearm_low * accepted.logical_not() * blocked.logical_not()
    rearm_reg.set(
        circuit.constant_signals({_REARM_STATE_SIGNAL: 1}).gate(accepted),
        when=accepted | clear_rearm,
    )

    # ``accepted`` is a physical response level and can remain high for multiple reactions.  Only
    # its first observation starts settlement; otherwise repeatedly rewriting the baseline can move
    # the goalpost after the product has already appeared and strand the controller forever.
    accept_start = accepted * await_stock.logical_not()
    output_visible = None
    for product_signal in _SETTLED_PRODUCTS:
        product_visible = (
            (settle_product.signal(product_signal) != 0)
            * (inventory.signal(product_signal) > baseline.signal(product_signal))
        )
        output_visible = product_visible if output_visible is None else output_visible | product_visible
    assert output_visible is not None
    output_visible = await_stock * idle * output_visible

    await_stock_reg.set(
        circuit.constant_signals({_AWAIT_STOCK_STATE_SIGNAL: 1}).gate(accept_start),
        when=accept_start | output_visible,
    )
    baseline_reg.set(inventory.gate(accept_start), when=accept_start)
    settle_product_reg.set(held_product.gate(accept_start), when=accept_start | output_visible)

    circuit.output("pool_offer_valid", valid)
    circuit.output("pool_offer_recipe", held_recipe)
    circuit.output("pool_offer_inputs", held_inputs)
    circuit.output("pool_offer_product", held_product)

    circuit.output("diag_offer_valid", valid)
    circuit.output("diag_blocked", blocked)
    circuit.output("diag_accepted", accepted)
    circuit.output("diag_busy_count", busy_count)
    circuit.output("diag_completion_count", completion_count)
    circuit.output("diag_reserved", reserved)
    circuit.output("diag_promised", promised)
    circuit.output("diag_settling", await_stock)
    circuit.output("diag_job_recipe", held_recipe)
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
        _Dock(
            "diag_job_recipe",
            "job_recipe",
            ComponentSide.NORTH,
            _slot_x(9),
            DevicePortDirection.OUTPUT,
            PayloadShape.VECTOR,
            WireColor.GREEN,
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
        label="Fast splitter raw-plate closed-loop controller",
    )


def build_fast_splitter_probe_component(worker_count: int = 1) -> ConstrainedComponent:
    """Compose the raw-plate controller with the validated seamed worker pool."""

    if worker_count <= 0:
        raise ValueError("worker_count must be positive")
    return compose_component_seams(
        _controller_component(),
        build_seamed_worker_pool_component(worker_count),
        left_seam="south_bus",
        right_seam="external",
        label=f"Fast splitter raw-plate mall probe — {worker_count} worker(s)",
    )


def build_fast_splitter_probe_blueprint(worker_count: int = 1) -> Blueprint:
    component = build_fast_splitter_probe_component(worker_count)
    blueprint = deepcopy(component.anchored.blueprint)
    blueprint["label"] = f"Fast splitter raw-plate mall probe — {worker_count} worker(s)"
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
