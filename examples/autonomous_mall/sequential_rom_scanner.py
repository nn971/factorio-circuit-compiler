"""Clocked sequential scanner for the compact autonomous-mall quality ROM.

This module is deliberately close to the intended Factorio controller structure.  It
executes the signal-keyed physical ROM page ABI instead of indexing rich target policy
objects directly:

1. latch a currently demanded target item;
2. scan target-keyed recipe-record pages from downstream to upstream;
3. try final-product recycling first;
4. for craft candidates, ask an external recipe reader for the exact same-quality
   ingredient vector;
5. dispatch the first feasible downstream/high-quality action;
6. after accepted work, restart from live demand/stock.

The scanner advances by explicit microsteps.  Recipe reading is an external handshake
because the eventual circuit will use a real assembler as the recipe oracle.  No LP is
called here and no recipe ingredient table is stored in the ROM.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping

from .model import Amount, Commodity, Quality
from .quality_policy_graph import QualityAction, QualityActionGraph, QualityActionKind
from .quality_policy_rom import QualityPolicyRom, RomRecipeRecord, RomSchedule
from .signal_keyed_policy_rom import (
    RecipeAddressVector,
    SignalKeyedPolicyPages,
    build_recipe_address_vector,
    build_signal_keyed_policy_pages,
)


class ScanMode(Enum):
    RECYCLE = "recycle"
    CRAFT = "craft"


class ScannerDecisionKind(Enum):
    IDLE = "idle"
    BUSY = "busy"
    ADVANCE = "advance"
    READ_RECIPE = "read-recipe"
    DISPATCH = "dispatch"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class RecipeReadRequest:
    """One request to the external recipe-reader assembler.

    ``product_item`` is the canonical product signal used to select the recipe.  Quality
    is kept explicit because the current compiler SignalId does not yet encode quality.
    """

    recipe_id: int
    product_item: str
    quality: Quality


@dataclass(frozen=True)
class RecipeReadResponse:
    request: RecipeReadRequest
    ingredients: Mapping[Commodity, Amount]

    def __post_init__(self) -> None:
        normalized: dict[Commodity, Amount] = {}
        for commodity, amount in self.ingredients.items():
            value = Fraction(amount)
            if value < 0:
                raise ValueError("recipe-reader ingredient amounts must be non-negative")
            if value:
                normalized[commodity] = value
        object.__setattr__(self, "ingredients", MappingProxyType(normalized))


@dataclass(frozen=True)
class SequentialDispatchIntent:
    demand_target: Commodity
    action: QualityAction
    record_index: int

    @property
    def inputs(self) -> Mapping[Commodity, Amount]:
        return self.action.inputs


@dataclass(frozen=True)
class ScannerDecision:
    kind: ScannerDecisionKind
    target: Commodity | None = None
    mode: ScanMode | None = None
    record_index: int | None = None
    quality: Quality | None = None
    read_request: RecipeReadRequest | None = None
    intent: SequentialDispatchIntent | None = None


class GraphRecipeReader:
    """Exact fake of the future reader assembler, backed only by the canonical DAG.

    Tests use this adapter to prove that the scanner itself does not need an ingredient
    ROM.  A Factorio AssemblerDevice adapter can later replace it without changing the
    scanner state machine.
    """

    def __init__(self, graph: QualityActionGraph, addresses: RecipeAddressVector) -> None:
        self.graph = graph
        self.addresses = addresses
        self._recipes = {recipe.name: recipe for recipe in graph.recipe_dag.recipes}
        self._recipe_names = tuple(recipe.name for recipe in graph.recipe_dag.recipes)

    def read(self, request: RecipeReadRequest) -> RecipeReadResponse:
        try:
            recipe_name = self._recipe_names[request.recipe_id]
        except IndexError as exc:
            raise KeyError(request.recipe_id) from exc
        recipe = self._recipes[recipe_name]
        if recipe.product != request.product_item:
            raise ValueError(
                "recipe-reader request item does not match canonical product: "
                f"{request.product_item!r} != {recipe.product!r}"
            )
        return RecipeReadResponse(
            request,
            {
                Commodity(item, request.quality): Fraction(amount)
                for item, amount in recipe.ingredients.items()
                if Fraction(amount) > 0
            },
        )


class SequentialRomScanner:
    """One-target-at-a-time ROM scanner with explicit clock-like microsteps.

    Record pointers use the *rectangular physical page index*, not target-local tuple
    indexing.  Shorter target programs therefore encounter invalid/missing high pages
    and skip them exactly as the circuit scanner will.
    """

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
        self.pages = pages or build_signal_keyed_policy_pages(rom)
        self.addresses = addresses or build_recipe_address_vector(graph, rom)
        self._recipe_id = {name: index for index, name in enumerate(rom.recipe_names)}
        self._profile_id = {
            (profile.productivity_modules, profile.quality_modules): index
            for index, profile in enumerate(rom.profiles)
        }
        self._craft_actions: dict[tuple[int, Quality, int], QualityAction] = {}
        self._recycle_actions: dict[tuple[int, Quality], QualityAction] = {}
        for action in graph.actions:
            recipe_id = self._recipe_id.get(action.recipe_name)
            if recipe_id is None:
                continue
            if action.kind is QualityActionKind.CRAFT:
                profile_id = self._profile_id.get(
                    (
                        action.module_profile.productivity_modules,
                        action.module_profile.quality_modules,
                    )
                )
                if profile_id is not None:
                    self._craft_actions[(recipe_id, action.base_quality, profile_id)] = action
            elif action.kind is QualityActionKind.RECYCLE:
                self._recycle_actions[(recipe_id, action.base_quality)] = action

        self._target: Commodity | None = None
        self._mode = ScanMode.RECYCLE
        self._record_index = self.pages.max_records - 1
        self._quality = Quality.EPIC
        self._pending_read: RecipeReadRequest | None = None
        self._schedule_dispatches: dict[tuple[Commodity, int, Quality], int] = {}
        self._profile_dispatches: dict[tuple[Commodity, int, Quality, int], int] = {}
        self._validate_rom_actions()

    @property
    def target(self) -> Commodity | None:
        return self._target

    @property
    def mode(self) -> ScanMode:
        return self._mode

    @property
    def record_index(self) -> int:
        return self._record_index

    @property
    def quality(self) -> Quality:
        return self._quality

    @property
    def pending_read(self) -> RecipeReadRequest | None:
        return self._pending_read

    def reset(self) -> None:
        self._target = None
        self._mode = ScanMode.RECYCLE
        self._record_index = self.pages.max_records - 1
        self._quality = Quality.EPIC
        self._pending_read = None

    def step(
        self,
        *,
        target: Commodity | None,
        stock: Mapping[Commodity, Amount],
        reader_response: RecipeReadResponse | None = None,
        busy: bool = False,
    ) -> ScannerDecision:
        """Advance the scanner by one externally observable microstep."""

        normalized_stock = _normalize_nonnegative(stock, label="stock")
        if target is None:
            self.reset()
            if reader_response is not None:
                raise ValueError("recipe-reader response supplied while scanner is idle")
            return ScannerDecision(ScannerDecisionKind.IDLE)
        if target not in self.rom.targets:
            raise ValueError(
                f"target {target.item}@{target.quality.name.lower()} is absent from ROM"
            )
        if busy:
            self.reset()
            if reader_response is not None:
                raise ValueError("recipe-reader response supplied while worker is busy")
            return ScannerDecision(ScannerDecisionKind.BUSY, target=target)

        if self._target != target:
            if reader_response is not None:
                raise ValueError("stale recipe-reader response after target change")
            self._restart(target)

        if self._mode is ScanMode.RECYCLE:
            if reader_response is not None:
                raise ValueError("recipe-reader response supplied during recycle scan")
            return self._step_recycle(normalized_stock)
        return self._step_craft(normalized_stock, reader_response)

    def record_dispatch(self, intent: SequentialDispatchIntent) -> None:
        """Advance weighted schedule phase only after a dispatcher accepts the intent."""

        if intent.demand_target not in self.rom.targets:
            raise ValueError("dispatch target is absent from ROM")
        action = intent.action
        recipe_id = self._recipe_id[action.recipe_name]
        if action.kind is QualityActionKind.CRAFT:
            profile_id = self._profile_id[
                (
                    action.module_profile.productivity_modules,
                    action.module_profile.quality_modules,
                )
            ]
            lane = (intent.demand_target, recipe_id, action.base_quality)
            self._schedule_dispatches[lane] = self._schedule_dispatches.get(lane, 0) + 1
            profile_lane = (*lane, profile_id)
            self._profile_dispatches[profile_lane] = (
                self._profile_dispatches.get(profile_lane, 0) + 1
            )
        self.reset()

    def _restart(self, target: Commodity) -> None:
        self._target = target
        self._mode = ScanMode.RECYCLE
        self._record_index = self.pages.max_records - 1
        self._quality = Quality.EPIC
        self._pending_read = None

    def _step_recycle(self, stock: Mapping[Commodity, Amount]) -> ScannerDecision:
        assert self._target is not None
        if self._record_index < 0:
            self._start_craft_scan()
            return self._advance_decision()

        record = self.pages.lookup_record(self._target, self._record_index)
        if record is None or not record.recycle_final:
            self._record_index -= 1
            return self._advance_decision()

        quality = self._quality
        action = self._recycle_actions.get((record.recipe_id, quality))
        if action is None:
            raise ValueError(
                "ROM final record lacks recycler action for "
                f"{self.addresses.item_for_recipe_id(record.recipe_id)}@{quality.name.lower()}"
            )
        if _inputs_available(action.inputs, stock):
            return ScannerDecision(
                ScannerDecisionKind.DISPATCH,
                target=self._target,
                mode=self._mode,
                record_index=self._record_index,
                quality=quality,
                intent=SequentialDispatchIntent(self._target, action, self._record_index),
            )

        if quality is Quality.NORMAL:
            self._start_craft_scan()
        else:
            self._quality = Quality(int(quality) - 1)
        return self._advance_decision()

    def _step_craft(
        self,
        stock: Mapping[Commodity, Amount],
        reader_response: RecipeReadResponse | None,
    ) -> ScannerDecision:
        assert self._target is not None
        if self._record_index < 0:
            if reader_response is not None:
                raise ValueError("recipe-reader response supplied after scan exhaustion")
            return ScannerDecision(
                ScannerDecisionKind.EXHAUSTED,
                target=self._target,
                mode=self._mode,
                record_index=self._record_index,
                quality=self._quality,
            )

        record = self.pages.lookup_record(self._target, self._record_index)
        if record is None:
            if reader_response is not None:
                raise ValueError("recipe-reader response supplied for an absent ROM record")
            self._advance_craft_candidate(record_finished=True)
            return self._advance_decision()

        quality = self._quality
        request = RecipeReadRequest(
            recipe_id=record.recipe_id,
            product_item=self.addresses.item_for_recipe_id(record.recipe_id),
            quality=quality,
        )
        if self._pending_read is None:
            if reader_response is not None:
                raise ValueError("unexpected recipe-reader response without a pending request")
            self._pending_read = request
            return ScannerDecision(
                ScannerDecisionKind.READ_RECIPE,
                target=self._target,
                mode=self._mode,
                record_index=self._record_index,
                quality=quality,
                read_request=request,
            )

        if self._pending_read != request:
            raise RuntimeError("scanner pending request no longer matches current candidate")
        if reader_response is None:
            return ScannerDecision(
                ScannerDecisionKind.READ_RECIPE,
                target=self._target,
                mode=self._mode,
                record_index=self._record_index,
                quality=quality,
                read_request=request,
            )
        if reader_response.request != request:
            raise ValueError("recipe-reader response does not match pending request")

        ingredients = self._validate_reader_response(record, reader_response)
        self._pending_read = None
        if _inputs_available(ingredients, stock):
            action = self._choose_craft_action(self._target, record, quality)
            return ScannerDecision(
                ScannerDecisionKind.DISPATCH,
                target=self._target,
                mode=self._mode,
                record_index=self._record_index,
                quality=quality,
                intent=SequentialDispatchIntent(self._target, action, self._record_index),
            )

        self._advance_craft_candidate(record_finished=False)
        return self._advance_decision()

    def _validate_reader_response(
        self,
        record: RomRecipeRecord,
        response: RecipeReadResponse,
    ) -> Mapping[Commodity, Amount]:
        recipe_name = self.rom.recipe_name(record.recipe_id)
        recipe = next(
            recipe for recipe in self.graph.recipe_dag.recipes if recipe.name == recipe_name
        )
        expected = {
            Commodity(item, response.request.quality): Fraction(amount)
            for item, amount in recipe.ingredients.items()
            if Fraction(amount) > 0
        }
        actual = dict(response.ingredients)
        if actual != expected:
            raise ValueError(
                "recipe-reader response does not match canonical recipe ingredients: "
                f"expected={expected!r}, actual={actual!r}"
            )
        if any(commodity.quality is not response.request.quality for commodity in actual):
            raise ValueError("recipe-reader returned ingredients at the wrong quality")
        return response.ingredients

    def _choose_craft_action(
        self,
        target: Commodity,
        record: RomRecipeRecord,
        quality: Quality,
    ) -> QualityAction:
        schedule = self.rom.schedules[record.schedule_for(quality)]
        profile_id = self._choose_profile(target, record.recipe_id, quality, schedule)
        try:
            return self._craft_actions[(record.recipe_id, quality, profile_id)]
        except KeyError as exc:
            raise ValueError(
                "ROM schedule selects unavailable craft action for "
                f"{self.addresses.item_for_recipe_id(record.recipe_id)}@{quality.name.lower()}"
            ) from exc

    def _choose_profile(
        self,
        target: Commodity,
        recipe_id: int,
        quality: Quality,
        schedule: RomSchedule,
    ) -> int:
        if len(schedule.choices) == 1:
            return schedule.choices[0].profile_id
        lane = (target, recipe_id, quality)
        dispatched = self._schedule_dispatches.get(lane, 0)

        def deficit(choice) -> tuple[Fraction, int]:
            served = self._profile_dispatches.get((*lane, choice.profile_id), 0)
            desired_after_next = Fraction(dispatched + 1) * choice.weight
            return (desired_after_next - served, -choice.profile_id)

        return max(schedule.choices, key=deficit).profile_id

    def _start_craft_scan(self) -> None:
        self._mode = ScanMode.CRAFT
        self._record_index = self.pages.max_records - 1
        self._quality = Quality.LEGENDARY
        self._pending_read = None

    def _advance_craft_candidate(self, *, record_finished: bool) -> None:
        self._pending_read = None
        if record_finished or self._quality is Quality.NORMAL:
            self._record_index -= 1
            self._quality = Quality.LEGENDARY
        else:
            self._quality = Quality(int(self._quality) - 1)

    def _advance_decision(self) -> ScannerDecision:
        return ScannerDecision(
            ScannerDecisionKind.ADVANCE,
            target=self._target,
            mode=self._mode,
            record_index=self._record_index,
            quality=self._quality,
        )

    def _validate_rom_actions(self) -> None:
        for target in self.rom.targets:
            for index in range(self.pages.max_records):
                record = self.pages.lookup_record(target, index)
                if record is None:
                    continue
                for quality in Quality:
                    schedule = self.rom.schedules[record.schedule_for(quality)]
                    for choice in schedule.choices:
                        key = (record.recipe_id, quality, choice.profile_id)
                        if key not in self._craft_actions:
                            raise ValueError(f"ROM scanner references absent craft action: {key}")
                if record.recycle_final:
                    for quality in (Quality.NORMAL, Quality.UNCOMMON, Quality.RARE, Quality.EPIC):
                        if (record.recipe_id, quality) not in self._recycle_actions:
                            raise ValueError(
                                "ROM scanner final record lacks recycler action: "
                                f"recipe={record.recipe_id}, quality={quality.name.lower()}"
                            )


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


def _inputs_available(
    required: Mapping[Commodity, Amount],
    stock: Mapping[Commodity, Amount],
) -> bool:
    return all(
        stock.get(commodity, Fraction(0)) >= Fraction(amount)
        for commodity, amount in required.items()
    )
