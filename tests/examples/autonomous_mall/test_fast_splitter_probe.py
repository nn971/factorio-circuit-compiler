from examples.autonomous_mall.fast_splitter_probe import (
    COPPER_CABLE,
    COPPER_CABLE_RECIPE,
    COPPER_PLATE,
    ELECTRONIC_CIRCUIT,
    ELECTRONIC_CIRCUIT_RECIPE,
    FAST_SPLITTER,
    FAST_SPLITTER_RECIPE,
    IRON_GEAR_WHEEL,
    IRON_GEAR_WHEEL_RECIPE,
    IRON_PLATE,
    SPLITTER,
    SPLITTER_RECIPE,
    TARGET_SIGNAL,
    TRANSPORT_BELT,
    TRANSPORT_BELT_RECIPE,
    build_fast_splitter_controller,
)
from factorio_circuit import SignalId
from factorio_circuit.simulate.semantic import simulate_stream


def _trace(rows):
    module = build_fast_splitter_controller().build()
    values = simulate_stream(module, rows)
    return [dict(zip(module.output.names, row, strict=True)) for row in values]


def _row(
    inventory,
    *,
    target=1,
    busy=0,
    accepted=0,
    blocked=0,
    promised=None,
):
    stock = dict(inventory)
    stock[TARGET_SIGNAL] = target
    return {
        "inventory": stock,
        "pool_blocked": blocked,
        "pool_accepted": accepted,
        "pool_busy_count": busy,
        "pool_completion_count": 0,
        "pool_reserved": {},
        "pool_promised": dict(promised or {}),
    }


def _selected_recipe(inventory):
    trace = _trace([_row(inventory), _row(inventory)])
    assert trace[1]["pool_offer_valid"] == 1
    recipe = trace[1]["pool_offer_recipe"]
    assert len(recipe) == 1
    return next(iter(recipe))


def test_raw_plates_start_by_making_copper_cable() -> None:
    recipe = _selected_recipe({IRON_PLATE: 46, COPPER_PLATE: 23})
    assert recipe == COPPER_CABLE_RECIPE


def test_dependency_selector_walks_the_fast_splitter_chain() -> None:
    assert _selected_recipe(
        {IRON_PLATE: 46, COPPER_PLATE: 22, COPPER_CABLE: 3}
    ) == ELECTRONIC_CIRCUIT_RECIPE

    assert _selected_recipe(
        {
            IRON_PLATE: 31,
            COPPER_PLATE: 0,
            ELECTRONIC_CIRCUIT: 15,
        }
    ) == IRON_GEAR_WHEEL_RECIPE

    assert _selected_recipe(
        {
            IRON_PLATE: 29,
            ELECTRONIC_CIRCUIT: 15,
            IRON_GEAR_WHEEL: 1,
        }
    ) == TRANSPORT_BELT_RECIPE

    assert _selected_recipe(
        {
            IRON_PLATE: 25,
            ELECTRONIC_CIRCUIT: 15,
            TRANSPORT_BELT: 4,
        }
    ) == SPLITTER_RECIPE

    assert _selected_recipe(
        {
            IRON_PLATE: 20,
            SPLITTER: 1,
            ELECTRONIC_CIRCUIT: 10,
        }
    ) == IRON_GEAR_WHEEL_RECIPE

    assert _selected_recipe(
        {
            SPLITTER: 1,
            IRON_GEAR_WHEEL: 10,
            ELECTRONIC_CIRCUIT: 10,
        }
    ) == FAST_SPLITTER_RECIPE


def test_latched_packet_does_not_change_while_valid_is_high() -> None:
    raw = {IRON_PLATE: 46, COPPER_PLATE: 23}
    cable_ready = {IRON_PLATE: 46, COPPER_PLATE: 22, COPPER_CABLE: 3}
    trace = _trace([
        _row(raw),
        _row(cable_ready),
        _row(cable_ready),
    ])

    # The first row chooses copper cable.  Even though inventory changes before acceptance and the
    # live selector would now prefer an electronic circuit, the packet presented with V=1 remains
    # the one that was latched at issue time.
    assert trace[1]["pool_offer_valid"] == 1
    assert trace[1]["pool_offer_recipe"] == {COPPER_CABLE_RECIPE: 1}
    assert trace[2]["pool_offer_recipe"] == {COPPER_CABLE_RECIPE: 1}


def test_stretched_accept_does_not_move_stock_baseline_and_deadlock() -> None:
    raw = {IRON_PLATE: 46, COPPER_PLATE: 23}
    cable_visible = {IRON_PLATE: 46, COPPER_PLATE: 22, COPPER_CABLE: 2}
    rows = [
        _row(raw),
        _row(raw),
        _row(raw, busy=1, accepted=1, promised={COPPER_CABLE: 2}),
        # Keep accepted physically high while the product becomes visible.  The old controller
        # rewrote its baseline here from cable=0 to cable=2 and could never settle afterward.
        _row(cable_visible, busy=1, accepted=1, promised={COPPER_CABLE: 2}),
        _row(cable_visible, busy=0),
        _row(cable_visible, busy=0),
        _row(cable_visible, busy=0),
    ]

    trace = _trace(rows)

    assert trace[2]["diag_accepted"] == 1
    assert trace[3]["diag_settling"] == 1
    assert trace[4]["diag_settling"] == 1
    assert trace[5]["diag_settling"] == 0
    # cable=2 is still short of the three needed for a circuit, so the controller should begin a
    # second cable craft instead of remaining permanently stuck after the first accepted job.
    assert trace[6]["pool_offer_valid"] == 1
    assert trace[6]["pool_offer_recipe"] == {COPPER_CABLE_RECIPE: 1}


def test_target_satisfied_stays_quiet() -> None:
    inventory = {FAST_SPLITTER: 1, IRON_PLATE: 100, COPPER_PLATE: 100}
    trace = _trace([_row(inventory, target=1) for _ in range(5)])
    assert all(row["pool_offer_valid"] == 0 for row in trace)


def test_worker_ledgers_still_pass_through_diagnostics() -> None:
    inventory = {IRON_PLATE: 46, COPPER_PLATE: 23}
    row = _row(inventory, busy=1, promised={COPPER_CABLE: 2})
    row["pool_reserved"] = {COPPER_PLATE: 1}

    result = _trace([row])[0]
    assert result["diag_busy_count"] == 1
    assert result["diag_reserved"] == {COPPER_PLATE: 1}
    assert result["diag_promised"] == {COPPER_CABLE: 2}


def test_explicit_recipe_signals_are_recipe_kind() -> None:
    for signal in (
        COPPER_CABLE_RECIPE,
        ELECTRONIC_CIRCUIT_RECIPE,
        IRON_GEAR_WHEEL_RECIPE,
        TRANSPORT_BELT_RECIPE,
        SPLITTER_RECIPE,
        FAST_SPLITTER_RECIPE,
    ):
        assert isinstance(signal, SignalId)
        assert signal.kind == "recipe"
