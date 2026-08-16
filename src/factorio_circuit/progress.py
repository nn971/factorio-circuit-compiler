"""Structured progress events for long-running compilation stages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompileProgress:
    """One observable compiler-progress update.

    ``completed``/``total`` are present when a stage has a meaningful finite denominator, such as
    physical wire routing.  ``detail`` is diagnostic text intended for CLIs and logs rather than a
    stable machine-readable sub-stage identifier.
    """

    phase: str
    completed: int | None = None
    total: int | None = None
    detail: str | None = None

    @property
    def fraction(self) -> float | None:
        """Return normalized completion for bounded stages, if known."""

        if self.completed is None or self.total is None or self.total <= 0:
            return None
        return min(1.0, max(0.0, self.completed / self.total))


ProgressCallback = Callable[[CompileProgress], None]


def report_progress(
    callback: ProgressCallback | None,
    phase: str,
    *,
    completed: int | None = None,
    total: int | None = None,
    detail: str | None = None,
) -> None:
    """Emit a progress event when observation was requested."""

    if callback is None:
        return
    callback(CompileProgress(phase, completed=completed, total=total, detail=detail))
