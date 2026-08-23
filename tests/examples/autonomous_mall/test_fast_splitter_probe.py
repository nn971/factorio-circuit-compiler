from examples.autonomous_mall.fast_splitter_probe import (
    ELECTRONIC_CIRCUIT,
    FAST_SPLITTER,
    FAST_SPLITTER_INPUTS,
    FAST_SPLITTER_PRODUCT,
    FAST_SPLITTER_RECIPE,
    FAST_SPLITTER_RECIPE_VECTOR,
    IRON_GEAR_WHEEL,
    SPLITTER,
    TARGET_SIGNAL,
    build_fast_splitter_controller,
)
from factorio_circuit.simulate.semantic import simulate_stream


def _trace(rows):
    module = build_fast_splitter_controller().build()
    values = simulate_stream(module, rows)
    return [dict(zip(module.output.names, row, strict=True)) for row in values]


def _row(*, stock=0, target=2, busy=0, accepted=0, blocked=0, ingredients=True):
    inventory = {
        FAST_SPLITTER: stock,
        TARGET_SIGNAL: target,
    }
    if ingredients:
        inventory.update(FAST_SPLITTER_INPUTS)
    return {
        "inventory": inventory,
        "pool_blocked": blocked,
        "pool_accepted": accepted,
        "pool_busy_count": busy,
        "pool_completion_count": 0,
        "pool_reserved": {},
        "pool_promised": {FAST_SPLITTER: 1} if busy else {},
    }


def test_controller_publishes_exact_fast_splitter_packet() -> None:
    trace = _trace([_row(), _row()])

    assert trace[0]["pool_offer_recipe"] == FAST_SPLITTER_RECIPE_VECTOR
    assert trace[0]["pool_offer_inputs"] == FAST_SPLITTER_INPUTS
    assert trace[0]["pool_offer_product"] == FAST_SPLITTER_PRODUCT
    assert FAST_SPLITTER_RECIPE.kind == "recipe"
    assert FAST_SPLITTER_RECIPE.name == "fast-splitter"


def test_controller_four_phase_rearms_and_waits_for_stock_visibility() -> None:
    rows = [
        _row(stock=0),
        _row(stock=0),
        _row(stock=0, busy=1, accepted=1),
        _row(stock=0, busy=1),
        # The assembler can become idle before its output is visible to the roboport.  Do not
        # schedule another job during that accounting gap.
        _row(stock=0, busy=0),
        _row(stock=1, busy=0),
        _row(stock=1, busy=0),
        _row(stock=1, busy=0),
    ]

    trace = _trace(rows)

    # First row decides to issue, second row presents V=1, accepted lowers V, then the controller
    # remains low until both the four-phase response and physical stock settlement are observed.
    assert [row["pool_offer_valid"] for row in trace] == [0, 1, 1, 0, 0, 0, 0, 1]
    assert trace[2]["diag_accepted"] == 1
    assert trace[3]["diag_settling"] == 1
    assert trace[4]["diag_settling"] == 1
    assert trace[5]["diag_settling"] == 1
    assert trace[6]["diag_settling"] == 0


def test_controller_does_not_issue_when_target_is_already_satisfied() -> None:
    trace = _trace([_row(stock=2, target=2) for _ in range(4)])

    assert all(row["pool_offer_valid"] == 0 for row in trace)


def test_controller_does_not_issue_without_all_recipe_ingredients() -> None:
    row = _row(ingredients=False)
    row["inventory"] = {
        FAST_SPLITTER: 0,
        TARGET_SIGNAL: 2,
        SPLITTER: 1,
        IRON_GEAR_WHEEL: 10,
        # electronic circuits deliberately absent
    }
    trace = _trace([row for _ in range(4)])

    assert ELECTRONIC_CIRCUIT not in row["inventory"]
    assert all(item["pool_offer_valid"] == 0 for item in trace)


def test_controller_passes_worker_ledgers_to_diagnostics() -> None:
    row = _row(stock=0, busy=1)
    row["pool_reserved"] = FAST_SPLITTER_INPUTS
    row["pool_promised"] = FAST_SPLITTER_PRODUCT

    result = _trace([row])[0]

    assert result["diag_busy_count"] == 1
    assert result["diag_reserved"] == FAST_SPLITTER_INPUTS
    assert result["diag_promised"] == FAST_SPLITTER_PRODUCT
