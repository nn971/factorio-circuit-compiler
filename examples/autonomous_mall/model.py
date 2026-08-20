"""Pure-Python economic model for the autonomous mall example."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from fractions import Fraction
from typing import Mapping

Amount = Fraction


class Quality(IntEnum):
    """Factorio quality tiers in increasing order."""

    NORMAL = 0
    UNCOMMON = 1
    RARE = 2
    EPIC = 3
    LEGENDARY = 4


@dataclass(frozen=True, order=True)
class Commodity:
    """An item together with its exact quality tier."""

    item: str
    quality: Quality = Quality.NORMAL


class WorkerKind(Enum):
    """Physically distinct mall worker pools."""

    PRODUCTIVITY = "productivity"
    QUALITY = "quality"
    RECYCLER = "recycler"


@dataclass(frozen=True)
class ProductionRoute:
    """One expected material transformation available to the planner.

    Both sides are arbitrary quality-qualified vectors. Fractions are deliberate:
    quality and recycling policies are represented by their expected material balance.
    """

    name: str
    worker_kind: WorkerKind
    inputs: Mapping[Commodity, Amount]
    outputs: Mapping[Commodity, Amount]

    def __post_init__(self) -> None:
        if any(Fraction(amount) < 0 for amount in self.inputs.values()):
            raise ValueError("route inputs must be non-negative")
        if any(Fraction(amount) < 0 for amount in self.outputs.values()):
            raise ValueError("route outputs must be non-negative")
        if not any(Fraction(amount) > 0 for amount in self.outputs.values()):
            raise ValueError("route must have at least one positive output")


class RecipeBook:
    """Collection of candidate expected routes."""

    def __init__(self, routes: list[ProductionRoute] | tuple[ProductionRoute, ...]) -> None:
        names: set[str] = set()
        normalized: list[ProductionRoute] = []
        for route in routes:
            if route.name in names:
                raise ValueError(f"duplicate route name: {route.name}")
            names.add(route.name)
            normalized.append(route)
        self._routes = tuple(sorted(normalized, key=lambda route: route.name))

    @property
    def routes(self) -> tuple[ProductionRoute, ...]:
        return self._routes
