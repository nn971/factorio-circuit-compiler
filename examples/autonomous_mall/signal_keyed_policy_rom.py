"""Signal-keyed physical page model for the compact quality-policy ROM.

Factorio 2.0 pairwise arithmetic can evaluate ``Each(red) op Each(green)`` by signal
identity.  That gives us an associative lookup primitive without a per-target decoder:

    selected target item (red, count=1)
        *
    ROM page {target-item: packed-word, ...} (green)
        ->
    selected target item carrying packed-word

A following ``Each + 0 -> scalar`` reduction turns that one surviving signal into an
ordinary numeric word.  This module models the page layout and exact lookup semantics;
physical combinator emission is intentionally deferred until vector pairwise lowering
is available in the compiler proper.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from .model import Commodity
from .quality_policy_rom import QualityPolicyRom, RomRecipeRecord

RomWordKind = Literal["descriptor", "schedules"]


@dataclass(frozen=True)
class SignalKeyedRomPage:
    record_index: int
    word_kind: RomWordKind
    entries: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.record_index < 0:
            raise ValueError("record_index must be non-negative")
        if self.word_kind not in {"descriptor", "schedules"}:
            raise ValueError(f"unknown ROM word kind {self.word_kind!r}")
        object.__setattr__(self, "entries", MappingProxyType(dict(self.entries)))

    def lookup(self, selected_item: str) -> int | None:
        """Semantic result after pairwise Each(red)*Each(green) and scalar reduction."""

        return self.entries.get(selected_item)

    def constant_chunks(self, *, slots: int = 20) -> tuple[tuple[tuple[str, int], ...], ...]:
        """Split one page into constant-combinator payloads.

        All chunks of one page share the same green ROM network, so their disjoint item
        signals sum into one page vector.  Twenty slots matches the Factorio constant
        combinator circuit-output capacity.
        """

        if slots <= 0:
            raise ValueError("slots must be positive")
        ordered = tuple(sorted(self.entries.items()))
        return tuple(
            ordered[index : index + slots]
            for index in range(0, len(ordered), slots)
        )


@dataclass(frozen=True)
class SignalKeyedPolicyPages:
    pages: tuple[SignalKeyedRomPage, ...]
    max_records: int

    def __post_init__(self) -> None:
        if self.max_records < 1:
            raise ValueError("max_records must be positive")
        expected = {
            (record_index, word_kind)
            for record_index in range(self.max_records)
            for word_kind in ("descriptor", "schedules")
        }
        actual = {(page.record_index, page.word_kind) for page in self.pages}
        if actual != expected:
            raise ValueError("signal-keyed ROM pages are incomplete or duplicated")

    def page(self, record_index: int, word_kind: RomWordKind) -> SignalKeyedRomPage:
        return next(
            page
            for page in self.pages
            if page.record_index == record_index and page.word_kind == word_kind
        )

    def lookup_record(self, target: Commodity, record_index: int) -> RomRecipeRecord | None:
        """Reconstruct one target-local recipe record through the hardware page ABI."""

        descriptor = self.page(record_index, "descriptor").lookup(target.item)
        schedules = self.page(record_index, "schedules").lookup(target.item)
        if descriptor is None and schedules is None:
            return None
        if descriptor is None or schedules is None:
            raise ValueError("partial target record in signal-keyed ROM pages")
        return RomRecipeRecord.unpack(descriptor, schedules)

    @property
    def constant_combinator_count(self) -> int:
        return sum(len(page.constant_chunks()) for page in self.pages)


def build_signal_keyed_policy_pages(rom: QualityPolicyRom) -> SignalKeyedPolicyPages:
    """Transpose target-local records into target-item-keyed physical ROM pages."""

    max_records = max(len(policy.records) for policy in rom.targets.values())
    pages: list[SignalKeyedRomPage] = []
    for record_index in range(max_records):
        descriptors: dict[str, int] = {}
        schedules: dict[str, int] = {}
        for target, policy in rom.targets.items():
            if record_index >= len(policy.records):
                continue
            descriptor, schedule_word = policy.records[record_index].pack()
            if target.item in descriptors:
                raise ValueError(f"duplicate target item signal in ROM: {target.item!r}")
            descriptors[target.item] = descriptor
            schedules[target.item] = schedule_word
        pages.append(SignalKeyedRomPage(record_index, "descriptor", descriptors))
        pages.append(SignalKeyedRomPage(record_index, "schedules", schedules))
    return SignalKeyedPolicyPages(tuple(pages), max_records)


def pairwise_each_lookup(
    *,
    selected: Mapping[str, int],
    rom_vector: Mapping[str, int],
) -> dict[str, int]:
    """Reference semantics for ``Each(red) * Each(green) -> Each``.

    Inputs are signal-name/count mappings from distinct networks.  Missing signals are
    zero.  The result preserves only signal identities present with non-zero values on
    both sides.  This is the exact operation used by the planned one-hot target lookup.
    """

    result: dict[str, int] = {}
    for signal in set(selected) | set(rom_vector):
        value = int(selected.get(signal, 0)) * int(rom_vector.get(signal, 0))
        if value:
            result[signal] = value
    return result


def reduce_single_signal(values: Mapping[str, int]) -> int:
    """Reference semantics for ``Each + 0 -> WORD`` after one-hot lookup."""

    return sum(int(value) for value in values.values())
