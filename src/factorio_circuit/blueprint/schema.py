"""Small blueprint-facing schema helpers."""

from __future__ import annotations

from typing import TypedDict


class Position(TypedDict):
    x: float
    y: float
