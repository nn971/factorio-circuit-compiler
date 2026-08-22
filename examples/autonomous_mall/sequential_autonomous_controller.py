"""Demand-flexible autonomous controller around the clocked sequential ROM scanner.

This is the closest Python reference to the intended in-game controller so far.  The
runtime uses only:

* current physical stock;
* current desired Legendary stock vector;
* compact quality-policy ROM pages/schedules;
* an external recipe-reader handshake;
* a small amount of fairness/scanner state.

There is no LP and no runtime ingredient table.  Demand is re-evaluated every microstep.
A target change invalidates any outstanding recipe-reader response, and an exhausted
blocked target never prevents another active target from being scanned.

One scan uses a transactional stock snapshot.  This avoids both starvation from noisy
roboport updates and internally inconsistent decisions where later candidates see a
different inventory than earlier candidates.  If the snapshot becomes stale before a
scan exhausts, exhaustion causes a restart on fresh stock rather than a false BLOCKED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping

from .model import Amount, Commodity
from .quality_policy_graph import QualityActionGraph
from .quality_policy_rom import QualityPolicyRom
from .sequential_rom_scanner import (
    RecipeReadRequest,
    RecipeReadResponse,
    ScannerDecisionKind,
    SequentialDispatchIntent,
    SequentialRomScanner,
)
from .signal_keyed_policy_rom import RecipeAddressVector, SignalKeyedPolicyPages


class SequentialControllerDecisionKind(Enum):
    SATISFIED = "satisfied"
    BUSY = "busy"
    SCANNING = "scanning"
    READ_RECIPE = "read-recipe"
    DISPATCH = "dispatch"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SequentialControllerDecision:
    kind: SequentialControllerDecisionKind
    selected_target: Commodity | None = None
    read_request: RecipeReadRequest | None = None
    intent: SequentialDispatchIntent | None = None
    blocked_targets: tuple[Commodity, ...] = ()
    blocked_on: Mapping[Commodity, Amount] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "blocked_on",
            MappingProxyType(
                {
                    commodity: Fraction(amount)
                    for commodity, amount in self.blocked_on.items()
                    if Fraction(amount) > 0
                }
            ),
        )


class SequentialAutonomousQualityController:
    """Autonomous variable-demand controller with explicit multi-tick scanning."""

    def __init__(
        self,
        graph: QualityActionGraph,
        rom: QualityPolicyRom,
        *,
        pages: SignalKeyedPolicyPages | None = None,
        addresses: RecipeAddressVector | None = None,
    ) -> None:
        self.graph = graph
        self.rom = rom
        self.scanner = SequentialRomScanner(
            graph,
            rom,
            pages=pages,
            addresses=addresses,
        )
        self._target_dispatches: dict[Commodity, int] = {}
        self._blocked_targets: set[Commodity] = set()
        self._observation_fingerprint: tuple[object, ...] | None = None
        self._last_missing: dict[Commodity, Amount] = {}
        self._scan_stock_snapshot: dict[Commodity, Amount] | None = None

    def step(
        self,
        *,
        stock: Mapping[Commodity, Amount],
        demands: Mapping[Commodity, Amount],
        reader_response: RecipeReadResponse | None = None,
        busy: bool = False,
    ) -> SequentialControllerDecision:
        """Advance one controller microstep using the latest live observations."""

        normalized_stock = _normalize_nonnegative(stock, label="stock")
        normalized_demands = _normalize_nonnegative(demands, label="demand")
        unknown = set(normalized_demands) - set(self.rom.targets)
        if unknown:
            rendered = ", ".join(
                f"{item.item}@{item.quality.name.lower()}"
                for item in sorted(unknown, key=lambda c: (c.item, int(c.quality)))
            )
            raise ValueError(f"demand contains targets absent from ROM: {rendered}")

        fingerprint = _fingerprint(normalized_stock, normalized_demands)
        if fingerprint != self._observation_fingerprint:
            self._blocked_targets.clear()
            self._last_missing.clear()
            self._observation_fingerprint = fingerprint

        deficits = {
            target: desired - normalized_stock.get(target, Fraction(0))
            for target, desired in normalized_demands.items()
            if normalized_stock.get(target, Fraction(0)) < desired
        }
        if not deficits:
            self._reset_scan()
            self._blocked_targets.clear()
            return SequentialControllerDecision(SequentialControllerDecisionKind.SATISFIED)

        if busy:
            self._reset_scan()
            return SequentialControllerDecision(SequentialControllerDecisionKind.BUSY)

        ordered = self._ordered_targets(deficits, normalized_demands)
        available = tuple(target for target in ordered if target not in self._blocked_targets)
        if not available:
            self._reset_scan()
            return SequentialControllerDecision(
                SequentialControllerDecisionKind.BLOCKED,
                blocked_targets=tuple(ordered),
                blocked_on=self._last_missing,
            )

        selected = available[0]
        # A reader response belongs to one latched target scan.  A target switch is an
        # explicit transaction boundary: discard any stale response and snapshot again.
        if self.scanner.target is not None and self.scanner.target != selected:
            reader_response = None
            self._reset_scan()

        if self._scan_stock_snapshot is None:
            self._scan_stock_snapshot = dict(normalized_stock)
        scan_stock = self._scan_stock_snapshot

        before_pending = self.scanner.pending_read
        scanner_decision = self.scanner.step(
            target=selected,
            stock=scan_stock,
            reader_response=reader_response,
        )

        if scanner_decision.kind is ScannerDecisionKind.READ_RECIPE:
            return SequentialControllerDecision(
                SequentialControllerDecisionKind.READ_RECIPE,
                selected_target=selected,
                read_request=scanner_decision.read_request,
            )
        if scanner_decision.kind is ScannerDecisionKind.DISPATCH:
            return SequentialControllerDecision(
                SequentialControllerDecisionKind.DISPATCH,
                selected_target=selected,
                intent=scanner_decision.intent,
            )
        if scanner_decision.kind is ScannerDecisionKind.EXHAUSTED:
            # If logistics changed while we were scanning the snapshot, restart against
            # the fresh stock rather than declaring a stale shortage authoritative.
            if scan_stock != normalized_stock:
                self._reset_scan()
                return SequentialControllerDecision(
                    SequentialControllerDecisionKind.SCANNING,
                    selected_target=selected,
                )
            self._blocked_targets.add(selected)
            self._reset_scan()
            return SequentialControllerDecision(
                SequentialControllerDecisionKind.SCANNING,
                selected_target=selected,
                blocked_targets=tuple(sorted(self._blocked_targets, key=_commodity_key)),
            )
        if scanner_decision.kind in {
            ScannerDecisionKind.ADVANCE,
            ScannerDecisionKind.IDLE,
        }:
            # If a response just proved a candidate infeasible, retain its missing
            # vector for diagnostics.  Use the scan snapshot because that is the stock
            # state against which the candidate was actually judged.
            if reader_response is not None and before_pending is not None:
                missing = {
                    commodity: Fraction(required) - scan_stock.get(commodity, Fraction(0))
                    for commodity, required in reader_response.ingredients.items()
                    if scan_stock.get(commodity, Fraction(0)) < Fraction(required)
                }
                if missing:
                    self._last_missing = missing
            return SequentialControllerDecision(
                SequentialControllerDecisionKind.SCANNING,
                selected_target=selected,
            )
        if scanner_decision.kind is ScannerDecisionKind.BUSY:  # pragma: no cover
            self._reset_scan()
            return SequentialControllerDecision(SequentialControllerDecisionKind.BUSY)
        raise AssertionError(scanner_decision.kind)

    def record_dispatch(self, intent: SequentialDispatchIntent) -> None:
        """Commit fairness/schedule state after the worker conveyor accepts a job."""

        self.scanner.record_dispatch(intent)
        self._scan_stock_snapshot = None
        target = intent.demand_target
        self._target_dispatches[target] = self._target_dispatches.get(target, 0) + 1
        self._blocked_targets.clear()
        self._last_missing.clear()

    def _reset_scan(self) -> None:
        self.scanner.reset()
        self._scan_stock_snapshot = None

    def _ordered_targets(
        self,
        deficits: Mapping[Commodity, Amount],
        demands: Mapping[Commodity, Amount],
    ) -> tuple[Commodity, ...]:
        def key(target: Commodity):
            pressure = Fraction(deficits[target]) / Fraction(demands[target])
            policy = self.rom.target_policy(target)
            return (
                -pressure,
                self._target_dispatches.get(target, 0),
                -policy.unit_raw_cost,
                target.item,
                int(target.quality),
            )

        return tuple(sorted(deficits, key=key))


def _normalize_nonnegative(
    values: Mapping[Commodity, Amount],
    *,
    label: str,
) -> dict[Commodity, Amount]:
    result: dict[Commodity, Amount] = {}
    for commodity, amount in values.items():
        value = Fraction(amount)
        if value < 0:
            raise ValueError(f"negative {label} for {commodity}")
        if value:
            result[commodity] = value
    return result


def _commodity_key(commodity: Commodity) -> tuple[str, int]:
    return (commodity.item, int(commodity.quality))


def _fingerprint(
    stock: Mapping[Commodity, Amount],
    demands: Mapping[Commodity, Amount],
) -> tuple[object, ...]:
    stock_items = tuple(
        sorted(
            ((item.item, int(item.quality), Fraction(amount)) for item, amount in stock.items()),
        )
    )
    demand_items = tuple(
        sorted(
            ((item.item, int(item.quality), Fraction(amount)) for item, amount in demands.items()),
        )
    )
    return (stock_items, demand_items)
