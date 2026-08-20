"""Physical sampling policies for external Level observations."""

from __future__ import annotations

from enum import StrEnum


class SamplingPolicy(StrEnum):
    """Choose when a live external Level source is observed within one logical occurrence.

    ``BEGINNING_OF_STEP`` preserves the historical snapshot semantics: phase-zero external values
    are exact tokens, so a later consumer may require physical delay combinators.

    ``ALAP`` keeps the external circuit-network value live and observes it as late as the physical
    consumer schedule permits.  Moving the observation within the same logical occurrence is free;
    explicit logical reindexing (for example ``.step(1)``) is not rewritten by this policy.
    """

    BEGINNING_OF_STEP = "beginning-of-step"
    ALAP = "alap"


__all__ = ["SamplingPolicy"]
