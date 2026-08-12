"""Legacy names for the removed decorated-function frontend."""

from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def circuit[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Reject new use of the former ``@circuit`` AST frontend with a migration message."""

    raise RuntimeError(
        "@circuit has been removed; use c = Circuit(name), c.input()/c.signals(), symbolic "
        "operators, and c.output()"
    )


def is_circuit(fn: object) -> bool:
    return False
