from factorio_circuit import SignalId, lower_to_abstract_physical
from factorio_circuit.simulate.semantic import simulate_stream

from examples.autonomous_mall.worker_pool import (
    build_worker_pool,
    build_worker_pool_probe_blueprint,
    worker_ports,
)

GEAR = SignalId("item", "iron-gear-wheel")
PLATE = SignalId("item", "iron-plate")
CABLE = SignalId("item", "copper-cable")
COPPER = SignalId("item", "copper-plate")


def _input_row(
    *,
    worker_count: int,
    valid: int = 0,
    recipe: dict[SignalId, int] | None = None,
    inputs: dict[SignalId, int] | None = None,
    product: dict[SignalId, int] | None = None,
    working: tuple[int, ...] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "offer_valid": valid,
        "offer_recipe": recipe or {},
        "offer_inputs": inputs or {},
        "offer_product": product or {},
    }
    states = working or (0,) * worker_count
    assert len(states) == worker_count
    for index, state in enumerate(states):
        row[worker_ports(index).working] = state
    return row


def _simulate(worker_count: int, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    module = build_worker_pool(worker_count).build()
    trace = simulate_stream(module, rows)
    names = module.output.names
    return [dict(zip(names, values, strict=True)) for values in trace]


def _gear_job(*, worker_count: int, valid: int, working: tuple[int, ...]) -> dict[str, object]:
    return _input_row(
        worker_count=worker_count,
        valid=valid,
        recipe={GEAR: 1},
        inputs={PLATE: 2},
        product={GEAR: 1},
        working=working,
    )


def test_worker_pool_abi_scales_only_with_worker_count() -> None:
    module = build_worker_pool(3).build()

    assert [item.name for item in module.inputs] == [
        "offer_valid",
        "worker_0_working",
        "worker_1_working",
        "worker_2_working",
    ]
    assert [item.name for item in module.vector_inputs] == [
        "offer_recipe",
        "offer_inputs",
        "offer_product",
    ]
    assert len(module.state_registers) == 1 + 3 * 3


def test_held_valid_envelope_is_claimed_exactly_once() -> None:
    rows = [
        _gear_job(worker_count=2, valid=1, working=(0, 0)),
        _gear_job(worker_count=2, valid=1, working=(0, 0)),
        _gear_job(worker_count=2, valid=1, working=(0, 0)),
    ]

    trace = _simulate(2, rows)

    assert trace[0]["worker_0_claim"] == 1
    assert trace[0]["worker_1_claim"] == 0
    assert trace[0]["offer_accepted"] == 1
    assert trace[0]["reserved"] == {PLATE: 2}
    assert trace[0]["promised"] == {GEAR: 1}
    assert sum(int(row["offer_accepted"]) for row in trace) == 1
    assert all(row["worker_1_claim"] == 0 for row in trace)


def test_second_envelope_goes_to_next_idle_worker() -> None:
    rows = [
        _gear_job(worker_count=2, valid=1, working=(0, 0)),
        _input_row(worker_count=2, working=(0, 0)),
        _input_row(
            worker_count=2,
            valid=1,
            recipe={CABLE: 1},
            inputs={COPPER: 1},
            product={CABLE: 2},
            working=(0, 0),
        ),
        _input_row(worker_count=2, working=(0, 0)),
    ]

    trace = _simulate(2, rows)

    assert trace[0]["worker_0_claim"] == 1
    assert trace[2]["worker_1_claim"] == 1
    assert trace[2]["worker_0_claim"] == 0
    assert trace[2]["reserved"] == {PLATE: 2, COPPER: 1}
    assert trace[2]["promised"] == {GEAR: 1, CABLE: 2}
    assert trace[2]["busy_count"] == 2


def test_recipe_is_withdrawn_after_start_and_ledgers_release_after_finish() -> None:
    rows = [
        _gear_job(worker_count=1, valid=1, working=(0,)),
        _input_row(worker_count=1, working=(0,)),
        _input_row(worker_count=1, working=(1,)),
        _input_row(worker_count=1, working=(1,)),
        _input_row(worker_count=1, working=(0,)),
        _input_row(worker_count=1, working=(0,)),
    ]

    trace = _simulate(1, rows)

    # The claim is visible immediately; held command state appears on the next reaction.
    assert trace[0]["worker_0_claim"] == 1
    assert trace[1]["worker_0_recipe"] == {GEAR: 1}
    assert trace[1]["worker_0_requester_demand"] == {PLATE: 2}
    assert trace[1]["worker_0_enable"] == 1

    # working=1 causes recipe withdrawal. The reservation/promise remain until working returns low.
    assert trace[3]["worker_0_recipe"] == {}
    assert trace[3]["worker_0_requester_demand"] == {}
    assert trace[3]["worker_0_enable"] == 1
    assert trace[3]["reserved"] == {PLATE: 2}
    assert trace[3]["promised"] == {GEAR: 1}

    assert trace[4]["worker_0_finished"] == 1
    assert trace[4]["completion_count"] == 1
    assert trace[5]["worker_0_enable"] == 0
    assert trace[5]["reserved"] == {}
    assert trace[5]["promised"] == {}


def test_blocked_envelope_remains_pending_until_worker_becomes_idle() -> None:
    rows = [
        _gear_job(worker_count=1, valid=1, working=(0,)),
        _input_row(worker_count=1, working=(0,)),
        _input_row(
            worker_count=1,
            valid=1,
            recipe={CABLE: 1},
            inputs={COPPER: 1},
            product={CABLE: 2},
            working=(1,),
        ),
        _input_row(
            worker_count=1,
            valid=1,
            recipe={CABLE: 1},
            inputs={COPPER: 1},
            product={CABLE: 2},
            working=(0,),
        ),
        _input_row(
            worker_count=1,
            valid=1,
            recipe={CABLE: 1},
            inputs={COPPER: 1},
            product={CABLE: 2},
            working=(0,),
        ),
    ]

    trace = _simulate(1, rows)

    assert trace[2]["offer_blocked"] == 1
    assert trace[2]["offer_accepted"] == 0
    assert trace[3]["worker_0_finished"] == 1
    assert trace[3]["offer_blocked"] == 1
    assert trace[4]["worker_0_claim"] == 1
    assert trace[4]["offer_accepted"] == 1
    assert trace[4]["offer_blocked"] == 0
    assert trace[4]["reserved"] == {COPPER: 1}
    assert trace[4]["promised"] == {CABLE: 2}


def test_worker_pool_lowers_to_physical_ir() -> None:
    lowered = lower_to_abstract_physical(build_worker_pool(2))

    assert lowered.clocked is False
    assert lowered.abstract_physical.combinators


def test_two_worker_probe_contains_two_real_assembler_devices() -> None:
    blueprint = build_worker_pool_probe_blueprint(2)
    entities = blueprint["entities"]
    wires = blueprint["wires"]

    assert sum(entity.get("name") == "assembling-machine-3" for entity in entities) == 2
    assert sum(entity.get("name") == "requester-chest" for entity in entities) == 2
    assert sum(entity.get("name") == "active-provider-chest" for entity in entities) == 2

    entity_ids = {int(entity["entity_number"]) for entity in entities}
    assert all(int(left) in entity_ids and int(right) in entity_ids for left, _lc, right, _rc in wires)
