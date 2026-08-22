"""Minimal shared value types for the retained autonomous-mall offline oracle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from fractions import Fraction

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
