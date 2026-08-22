import pytest

from examples.autonomous_mall import (
    Commodity,
    Job,
    Quality,
    ReservationError,
    Scheduler,
    Worker,
    WorkerKind,
)


def c(item: str, quality: Quality = Quality.NORMAL) -> Commodity:
    return Commodity(item, quality)


def test_reservation_prevents_two_workers_from_spending_same_stock() -> None:
    gear = c("iron-gear-wheel")
    target = c("assembling-machine")
    scheduler = Scheduler(
        [
            Worker("p0", WorkerKind.PRODUCTIVITY),
            Worker("p1", WorkerKind.PRODUCTIVITY),
        ],
        stock={gear: 5},
    )

    first = Job("j0", WorkerKind.PRODUCTIVITY, "craft", target, {gear: 3})
    second = Job("j1", WorkerKind.PRODUCTIVITY, "craft", target, {gear: 3})

    assert scheduler.dispatch(first) == "p0"
    with pytest.raises(ReservationError):
        scheduler.dispatch(second)

    assert scheduler.workers["p1"].active_job is None
    assert scheduler.ledger.available(gear) == 2


def test_worker_pools_remain_physically_distinct() -> None:
    plate = c("iron-plate")
    rare_gear = c("iron-gear-wheel", Quality.RARE)
    scheduler = Scheduler(
        [
            Worker("p0", WorkerKind.PRODUCTIVITY),
            Worker("q0", WorkerKind.QUALITY),
            Worker("r0", WorkerKind.RECYCLER),
        ],
        stock={plate: 100},
    )

    quality_job = Job("q", WorkerKind.QUALITY, "craft", rare_gear, {plate: 5})
    assert scheduler.dispatch(quality_job) == "q0"
    assert scheduler.workers["p0"].active_job is None
    assert scheduler.workers["r0"].active_job is None


def test_stochastic_campaign_lock_prevents_speculative_duplicate_jobs() -> None:
    plate = c("iron-plate")
    rare_gear = c("iron-gear-wheel", Quality.RARE)
    scheduler = Scheduler(
        [Worker("q0", WorkerKind.QUALITY), Worker("q1", WorkerKind.QUALITY)],
        stock={plate: 100},
    )
    first = Job(
        "q0-job",
        WorkerKind.QUALITY,
        "craft",
        rare_gear,
        {plate: 5},
        campaign="rare-gear",
    )
    duplicate = Job(
        "q1-job",
        WorkerKind.QUALITY,
        "craft",
        rare_gear,
        {plate: 5},
        campaign="rare-gear",
    )

    assert scheduler.dispatch(first) == "q0"
    assert scheduler.dispatch(duplicate) is None
    assert scheduler.workers["q1"].active_job is None

    scheduler.finish("q0", outputs={rare_gear: 1})
    assert scheduler.dispatch(duplicate) == "q0"


def test_independent_quality_campaigns_can_run_in_parallel() -> None:
    plate = c("iron-plate")
    rare_gear = c("iron-gear-wheel", Quality.RARE)
    rare_circuit = c("electronic-circuit", Quality.RARE)
    scheduler = Scheduler(
        [Worker("q0", WorkerKind.QUALITY), Worker("q1", WorkerKind.QUALITY)],
        stock={plate: 100},
    )

    gear_job = Job(
        "gear",
        WorkerKind.QUALITY,
        "craft",
        rare_gear,
        {plate: 5},
        campaign="rare-gear",
    )
    circuit_job = Job(
        "circuit",
        WorkerKind.QUALITY,
        "craft",
        rare_circuit,
        {plate: 7},
        campaign="rare-circuit",
    )

    assert scheduler.dispatch(gear_job) == "q0"
    assert scheduler.dispatch(circuit_job) == "q1"


def test_actual_stochastic_output_is_committed_before_campaign_reopens() -> None:
    plate = c("iron-plate")
    rare_gear = c("iron-gear-wheel", Quality.RARE)
    epic_gear = c("iron-gear-wheel", Quality.EPIC)
    scheduler = Scheduler(
        [Worker("q0", WorkerKind.QUALITY)],
        stock={plate: 10},
    )
    job = Job(
        "quality-attempt",
        WorkerKind.QUALITY,
        "craft",
        rare_gear,
        {plate: 5},
        campaign="rare-gear",
    )

    assert scheduler.dispatch(job) == "q0"
    scheduler.finish("q0", outputs={epic_gear: 1})

    assert scheduler.ledger.stock == {plate: 5, epic_gear: 1}
