"""Transactional multi-worker scheduler model for the autonomous mall example."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Mapping

from .model import Amount, Commodity, WorkerKind


class ReservationError(RuntimeError):
    """A job cannot atomically reserve all required stock."""


@dataclass(frozen=True)
class Job:
    """One atomic physical attempt.

    Stochastic quality work is deliberately represented as one attempt per job; the
    controller observes the real output and replans before issuing another attempt.
    ``campaign`` prevents two workers from speculating on the same stochastic target.
    """

    job_id: str
    worker_kind: WorkerKind
    operation: str
    target: Commodity
    inputs: Mapping[Commodity, Amount]
    campaign: str | None = None


@dataclass
class Worker:
    worker_id: str
    kind: WorkerKind
    active_job: str | None = None


@dataclass
class ReservationLedger:
    """Confirmed stock plus atomic reservations for active jobs."""

    stock: dict[Commodity, Amount]
    _by_job: dict[str, dict[Commodity, Amount]] = field(default_factory=dict)

    def available(self, commodity: Commodity) -> Amount:
        reserved = sum(
            (reservation.get(commodity, Fraction(0)) for reservation in self._by_job.values()),
            start=Fraction(0),
        )
        return self.stock.get(commodity, Fraction(0)) - reserved

    def reserve(self, job_id: str, inputs: Mapping[Commodity, Amount]) -> None:
        if job_id in self._by_job:
            raise ReservationError(f"job already reserved: {job_id}")
        required = {
            commodity: Fraction(amount)
            for commodity, amount in inputs.items()
            if amount > 0
        }
        missing = {
            commodity: amount - self.available(commodity)
            for commodity, amount in required.items()
            if self.available(commodity) < amount
        }
        if missing:
            details = ", ".join(
                f"{commodity.item}:{commodity.quality.name}={amount}"
                for commodity, amount in sorted(missing.items())
            )
            raise ReservationError(f"insufficient unreserved stock: {details}")
        self._by_job[job_id] = required

    def finish(self, job_id: str, *, outputs: Mapping[Commodity, Amount]) -> None:
        reservation = self._by_job.pop(job_id, None)
        if reservation is None:
            raise ReservationError(f"job has no reservation: {job_id}")
        for commodity, amount in reservation.items():
            new_amount = self.stock.get(commodity, Fraction(0)) - amount
            if new_amount < 0:
                raise ReservationError(f"reservation exceeds confirmed stock for {commodity}")
            if new_amount:
                self.stock[commodity] = new_amount
            else:
                self.stock.pop(commodity, None)
        for commodity, amount in outputs.items():
            value = Fraction(amount)
            if value < 0:
                raise ValueError("job outputs must be non-negative")
            if value:
                self.stock[commodity] = self.stock.get(commodity, Fraction(0)) + value

    def cancel(self, job_id: str) -> None:
        if self._by_job.pop(job_id, None) is None:
            raise ReservationError(f"job has no reservation: {job_id}")


class Scheduler:
    """Allocate atomic jobs to physically distinct worker pools without oscillation."""

    def __init__(self, workers: list[Worker], *, stock: Mapping[Commodity, Amount]) -> None:
        ids = [worker.worker_id for worker in workers]
        if len(ids) != len(set(ids)):
            raise ValueError("worker IDs must be unique")
        self.workers = {worker.worker_id: worker for worker in workers}
        self.ledger = ReservationLedger(
            {commodity: Fraction(amount) for commodity, amount in stock.items() if amount > 0}
        )
        self.jobs: dict[str, Job] = {}
        self._active_campaigns: set[str] = set()

    def dispatch(self, job: Job) -> str | None:
        """Reserve inputs and assign the first compatible free worker.

        Returns the worker ID, or ``None`` if the worker pool/campaign is currently
        busy. A reservation failure raises ``ReservationError`` and leaves all state
        unchanged.
        """

        if job.job_id in self.jobs:
            raise ValueError(f"duplicate job ID: {job.job_id}")
        if job.campaign is not None and job.campaign in self._active_campaigns:
            return None

        worker = next(
            (
                candidate
                for candidate in self.workers.values()
                if candidate.kind is job.worker_kind and candidate.active_job is None
            ),
            None,
        )
        if worker is None:
            return None

        self.ledger.reserve(job.job_id, job.inputs)
        worker.active_job = job.job_id
        self.jobs[job.job_id] = job
        if job.campaign is not None:
            self._active_campaigns.add(job.campaign)
        return worker.worker_id

    def finish(
        self,
        worker_id: str,
        *,
        outputs: Mapping[Commodity, Amount],
    ) -> Job:
        worker = self.workers[worker_id]
        if worker.active_job is None:
            raise ValueError(f"worker is idle: {worker_id}")
        job = self.jobs.pop(worker.active_job)
        self.ledger.finish(job.job_id, outputs=outputs)
        worker.active_job = None
        if job.campaign is not None:
            self._active_campaigns.remove(job.campaign)
        return job

    def cancel(self, worker_id: str) -> Job:
        worker = self.workers[worker_id]
        if worker.active_job is None:
            raise ValueError(f"worker is idle: {worker_id}")
        job = self.jobs.pop(worker.active_job)
        self.ledger.cancel(job.job_id)
        worker.active_job = None
        if job.campaign is not None:
            self._active_campaigns.remove(job.campaign)
        return job
