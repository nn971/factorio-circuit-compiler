"""Physical sampling policies for external and derived Level observations."""

from __future__ import annotations

from enum import StrEnum


class SamplingPolicy(StrEnum):
    """Choose when a live Level is observed within one logical occurrence.

    ``BEGINNING_OF_STEP`` preserves the historical snapshot semantics: phase-zero external values
    are exact tokens, so a later consumer may require physical delay combinators.

    ``ALAP`` is a freshness relaxation. External circuit-network values remain live, and ordinary
    feed-forward Level logic derived from them may remain live as well. Each use may therefore
    observe the same physical Level representation as late as its consumer schedule permits,
    intentionally selecting the value present at that later physical tick. Different uses may
    observe at different ticks. Code that requires one coherent chosen token must establish that
    boundary explicitly; exact transport then preserves that token rather than re-observing it.

    Explicit logical reindexing such as ``.step(1)`` remains a logical-occurrence operation and is
    never rewritten by this policy.
    """

    BEGINNING_OF_STEP = "beginning-of-step"
    ALAP = "alap"


__all__ = ["SamplingPolicy"]
