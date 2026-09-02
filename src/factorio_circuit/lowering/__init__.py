"""Lowering package hooks for DAG-aware normalization.

The frontend semantic IR is an immutable DAG. ``_Normalizer._root_clock`` historically walked it
recursively as a tree and is invoked for every declared scalar operation during normalization.
Selector-heavy workloads such as the 2048 benchmark therefore repeatedly expanded the same shared
subexpressions and could spend minutes in the frontend normalization phase.

Install one identity cache per ``_Normalizer`` instance. The original implementation remains the
authoritative clock-selection logic; recursive calls dispatch back through this wrapper, so every
node's root clock is computed at most once for the lifetime of the normalizer.
"""

from __future__ import annotations

from typing import Any, cast

from factorio_circuit.ir.semantic import Clock

from . import frontend_to_ir as _frontend_to_ir

_original_root_clock = _frontend_to_ir._Normalizer._root_clock


def _memoized_root_clock(self: Any, value: object) -> Clock:
    cache: dict[int, Clock] | None = getattr(self, "_root_clock_cache", None)
    if cache is None:
        cache = {}
        self._root_clock_cache = cache

    key = id(value)
    cached = cache.get(key)
    if cached is not None:
        return cached

    result = _original_root_clock(self, value)
    cache[key] = result
    return result


cast(Any, _frontend_to_ir._Normalizer)._root_clock = _memoized_root_clock
