from __future__ import annotations

from examples.autonomous_mall.compiled_quality_policy import compile_quality_policy_book
from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_policy_graph import build_quality_action_graph
from examples.autonomous_mall.quality_policy_rom import compile_quality_policy_rom
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag
from examples.autonomous_mall.rom_quality_controller import RomAutonomousQualityController
from examples.autonomous_mall.sequential_rom_scanner import (
    GraphRecipeReader,
    RecipeReadResponse,
    ScanMode,
    ScannerDecisionKind,
    SequentialRomScanner,
)
from examples.autonomous_mall.signal_keyed_policy_rom import (
    build_recipe_address_vector,
    build_signal_keyed_policy_pages,
)


def _fixture(*, include_gear_target: bool = False):
    recipes = [
        ItemRecipe(
            "gear",
            "gear",
            1,
            {"iron": 2},
            allow_productivity=True,
            allow_quality=True,
        ),
        ItemRecipe(
            "machine",
            "machine",
            1,
            {"gear": 1},
            allow_productivity=False,
            allow_quality=True,
        ),
    ]
    targets = ["machine"]
    if include_gear_target:
        targets.insert(0, "gear")
    dag = build_recipe_dag(
        RecipeCatalog(recipes),
        targets=targets,
        raw_items={"iron"},
    )
    graph = build_quality_action_graph(dag)
    book = compile_quality_policy_book(graph)
    rom = compile_quality_policy_rom(graph, book)
    pages = build_signal_keyed_policy_pages(rom)
    addresses = build_recipe_address_vector(graph, rom)
    scanner = SequentialRomScanner(graph, rom, pages=pages, addresses=addresses)
    reader = GraphRecipeReader(graph, addresses)
    return graph, rom, pages, addresses, scanner, reader


def _drive_to_terminal(scanner, reader, *, target, stock, limit: int = 100):
    response = None
    trace = []
    for _ in range(limit):
        decision = scanner.step(
            target=target,
            stock=stock,
            reader_response=response,
        )
        trace.append(decision)
        response = None
        if decision.kind is ScannerDecisionKind.READ_RECIPE:
            assert decision.read_request is not None
            response = reader.read(decision.read_request)
        elif decision.kind in {
            ScannerDecisionKind.DISPATCH,
            ScannerDecisionKind.EXHAUSTED,
        }:
            return decision, trace
    raise AssertionError("scanner did not terminate within microstep limit")


def test_final_reject_dispatches_recycler_without_recipe_reader() -> None:
    _, _, _, _, scanner, _ = _fixture()
    target = Commodity("machine", Quality.LEGENDARY)
    stock = {Commodity("machine", Quality.RARE): 1}

    decision = None
    trace = []
    for _ in range(10):
        decision = scanner.step(target=target, stock=stock)
        trace.append(decision)
        if decision.kind is ScannerDecisionKind.DISPATCH:
            break

    assert decision is not None
    assert decision.kind is ScannerDecisionKind.DISPATCH
    assert decision.intent is not None
    assert decision.intent.action.kind.value == "recycle"
    assert decision.intent.action.base_quality is Quality.RARE
    assert all(item.kind is not ScannerDecisionKind.READ_RECIPE for item in trace)


def test_craft_scan_reads_downstream_recipe_at_same_quality() -> None:
    _, _, _, _, scanner, reader = _fixture()
    target = Commodity("machine", Quality.LEGENDARY)
    stock = {Commodity("gear", Quality.EPIC): 1}

    terminal, trace = _drive_to_terminal(
        scanner,
        reader,
        target=target,
        stock=stock,
    )

    assert terminal.kind is ScannerDecisionKind.DISPATCH
    assert terminal.intent is not None
    assert terminal.intent.action.recipe_name == "machine"
    assert terminal.intent.action.base_quality is Quality.EPIC

    requests = [item.read_request for item in trace if item.read_request is not None]
    assert requests
    assert requests[0].product_item == "machine"
    assert requests[0].quality is Quality.LEGENDARY
    assert any(request.quality is Quality.EPIC for request in requests)
    assert all(
        commodity.quality is request.quality
        for item in trace
        if item.read_request is not None
        for request in [item.read_request]
        for commodity in reader.read(request).ingredients
    )


def test_normal_raw_entry_eventually_scans_to_upstream_normal_recipe() -> None:
    _, _, _, _, scanner, reader = _fixture()
    target = Commodity("machine", Quality.LEGENDARY)
    stock = {Commodity("iron", Quality.NORMAL): 100}

    terminal, trace = _drive_to_terminal(
        scanner,
        reader,
        target=target,
        stock=stock,
    )

    assert terminal.kind is ScannerDecisionKind.DISPATCH
    assert terminal.intent is not None
    assert terminal.intent.action.recipe_name == "gear"
    assert terminal.intent.action.base_quality is Quality.NORMAL
    assert any(
        item.kind is ScannerDecisionKind.READ_RECIPE
        and item.read_request is not None
        and item.read_request.product_item == "machine"
        for item in trace
    )
    assert scanner.mode is ScanMode.CRAFT


def test_scanner_matches_direct_rom_controller_action() -> None:
    graph, rom, _, _, scanner, reader = _fixture()
    direct = RomAutonomousQualityController(graph, rom)
    target = Commodity("machine", Quality.LEGENDARY)

    cases = [
        {Commodity("iron", Quality.NORMAL): 100},
        {Commodity("gear", Quality.EPIC): 1},
        {
            Commodity("machine", Quality.UNCOMMON): 1,
            Commodity("iron", Quality.NORMAL): 100,
        },
    ]
    for stock in cases:
        scanner.reset()
        terminal, _ = _drive_to_terminal(scanner, reader, target=target, stock=stock)
        direct_decision = direct.decide(stock=stock, demands={target: 1})

        assert terminal.kind is ScannerDecisionKind.DISPATCH
        assert terminal.intent is not None
        assert direct_decision.intent is not None
        assert terminal.intent.action.name == direct_decision.intent.action.name


def test_short_target_program_skips_missing_rectangular_pages() -> None:
    _, rom, pages, _, scanner, reader = _fixture(include_gear_target=True)
    target = Commodity("gear", Quality.LEGENDARY)
    assert len(rom.target_policy(target).records) == 1
    assert pages.max_records == 2
    assert pages.lookup_record(target, 1) is None

    stock = {Commodity("iron", Quality.NORMAL): 100}
    terminal, trace = _drive_to_terminal(scanner, reader, target=target, stock=stock)

    assert terminal.kind is ScannerDecisionKind.DISPATCH
    assert terminal.intent is not None
    assert terminal.intent.action.recipe_name == "gear"
    assert any(
        item.kind is ScannerDecisionKind.ADVANCE and item.record_index == 0
        for item in trace
    )


def test_reader_response_with_wrong_quality_is_rejected() -> None:
    _, _, _, _, scanner, reader = _fixture()
    target = Commodity("machine", Quality.LEGENDARY)
    stock = {Commodity("iron", Quality.NORMAL): 100}

    response = None
    request = None
    for _ in range(20):
        decision = scanner.step(target=target, stock=stock, reader_response=response)
        response = None
        if decision.kind is ScannerDecisionKind.READ_RECIPE:
            request = decision.read_request
            break
    assert request is not None

    correct = reader.read(request)
    wrong = RecipeReadResponse(
        request,
        {
            Commodity(commodity.item, Quality.NORMAL): amount
            for commodity, amount in correct.ingredients.items()
        },
    )
    if request.quality is Quality.NORMAL:
        wrong = RecipeReadResponse(
            request,
            {
                Commodity(commodity.item, Quality.EPIC): amount
                for commodity, amount in correct.ingredients.items()
            },
        )

    try:
        scanner.step(target=target, stock=stock, reader_response=wrong)
    except ValueError as exc:
        assert "canonical recipe ingredients" in str(exc) or "wrong quality" in str(exc)
    else:
        raise AssertionError("wrong-quality recipe-reader response was accepted")


def test_target_change_invalidates_pending_reader_response() -> None:
    recipes = [
        ItemRecipe("gear", "gear", 1, {"iron": 2}, allow_productivity=True),
        ItemRecipe("machine-a", "machine-a", 1, {"gear": 1}),
        ItemRecipe("machine-b", "machine-b", 1, {"gear": 1}),
    ]
    dag = build_recipe_dag(
        RecipeCatalog(recipes),
        targets=["machine-a", "machine-b"],
        raw_items={"iron"},
    )
    graph = build_quality_action_graph(dag)
    book = compile_quality_policy_book(graph)
    rom = compile_quality_policy_rom(graph, book)
    addresses = build_recipe_address_vector(graph, rom)
    scanner = SequentialRomScanner(graph, rom, addresses=addresses)
    reader = GraphRecipeReader(graph, addresses)
    a = Commodity("machine-a", Quality.LEGENDARY)
    b = Commodity("machine-b", Quality.LEGENDARY)

    response = None
    pending = None
    for _ in range(20):
        decision = scanner.step(target=a, stock={}, reader_response=response)
        response = None
        if decision.kind is ScannerDecisionKind.READ_RECIPE:
            pending = reader.read(decision.read_request)
            break
    assert pending is not None

    try:
        scanner.step(target=b, stock={}, reader_response=pending)
    except ValueError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("stale reader response survived target change")
