from __future__ import annotations

from examples.autonomous_mall.compiled_quality_policy import compile_quality_policy_book
from examples.autonomous_mall.model import Commodity, Quality
from examples.autonomous_mall.quality_policy_graph import build_quality_action_graph
from examples.autonomous_mall.quality_policy_rom import compile_quality_policy_rom
from examples.autonomous_mall.recipe_graph import ItemRecipe, RecipeCatalog, build_recipe_dag
from examples.autonomous_mall.rom_quality_controller import RomAutonomousQualityController
from examples.autonomous_mall.sequential_autonomous_controller import (
    SequentialAutonomousQualityController,
    SequentialControllerDecisionKind,
)
from examples.autonomous_mall.sequential_rom_scanner import GraphRecipeReader
from examples.autonomous_mall.signal_keyed_policy_rom import build_recipe_address_vector


def _fixture_two_shared_targets():
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
    sequential = SequentialAutonomousQualityController(graph, rom, addresses=addresses)
    reader = GraphRecipeReader(graph, addresses)
    direct = RomAutonomousQualityController(graph, rom)
    return graph, rom, sequential, reader, direct


def _drive_until_dispatch(controller, reader, *, stock, demands, limit: int = 200):
    response = None
    trace = []
    for _ in range(limit):
        decision = controller.step(
            stock=stock,
            demands=demands,
            reader_response=response,
        )
        trace.append(decision)
        response = None
        if decision.kind is SequentialControllerDecisionKind.READ_RECIPE:
            assert decision.read_request is not None
            response = reader.read(decision.read_request)
        elif decision.kind in {
            SequentialControllerDecisionKind.DISPATCH,
            SequentialControllerDecisionKind.BLOCKED,
            SequentialControllerDecisionKind.SATISFIED,
        }:
            return decision, trace
    raise AssertionError("sequential controller did not reach terminal decision")


def test_sequential_controller_matches_direct_rom_action() -> None:
    _, _, sequential, reader, direct = _fixture_two_shared_targets()
    target = Commodity("machine-a", Quality.LEGENDARY)
    cases = [
        {Commodity("iron", Quality.NORMAL): 100},
        {Commodity("gear", Quality.EPIC): 1},
        {
            Commodity("machine-a", Quality.RARE): 1,
            Commodity("iron", Quality.NORMAL): 100,
        },
    ]

    for stock in cases:
        sequential.scanner.reset()
        decision, _ = _drive_until_dispatch(
            sequential,
            reader,
            stock=stock,
            demands={target: 1},
        )
        expected = direct.decide(stock=stock, demands={target: 1})
        assert decision.kind is SequentialControllerDecisionKind.DISPATCH
        assert decision.intent is not None
        assert expected.intent is not None
        assert decision.intent.action.name == expected.intent.action.name


def test_live_demand_change_discards_stale_reader_response() -> None:
    _, _, controller, reader, _ = _fixture_two_shared_targets()
    a = Commodity("machine-a", Quality.LEGENDARY)
    b = Commodity("machine-b", Quality.LEGENDARY)
    stock = {Commodity("iron", Quality.NORMAL): 100}

    response = None
    stale = None
    for _ in range(40):
        decision = controller.step(
            stock=stock,
            demands={a: 1},
            reader_response=response,
        )
        response = None
        if decision.kind is SequentialControllerDecisionKind.READ_RECIPE:
            assert decision.read_request is not None
            stale = reader.read(decision.read_request)
            break
    assert stale is not None

    # Demand switches before the external reader response returns.  The old response is
    # intentionally supplied anyway; the autonomous wrapper must discard it and restart
    # rather than forcing the old target campaign to continue.
    switched = controller.step(
        stock=stock,
        demands={b: 1},
        reader_response=stale,
    )
    assert switched.selected_target == b

    response = None
    if switched.kind is SequentialControllerDecisionKind.READ_RECIPE:
        response = reader.read(switched.read_request)
    terminal = None
    for _ in range(100):
        decision = controller.step(
            stock=stock,
            demands={b: 1},
            reader_response=response,
        )
        response = None
        if decision.kind is SequentialControllerDecisionKind.READ_RECIPE:
            response = reader.read(decision.read_request)
        elif decision.kind is SequentialControllerDecisionKind.DISPATCH:
            terminal = decision
            break
    assert terminal is not None
    assert terminal.selected_target == b


def test_blocked_high_priority_target_does_not_stall_other_demand() -> None:
    recipes = [
        ItemRecipe("expensive-a", "expensive-a", 1, {"steel": 10}),
        ItemRecipe("cheap-b", "cheap-b", 1, {"iron": 1}),
    ]
    dag = build_recipe_dag(
        RecipeCatalog(recipes),
        targets=["expensive-a", "cheap-b"],
        raw_items={"iron", "steel"},
    )
    graph = build_quality_action_graph(dag)
    book = compile_quality_policy_book(graph)
    rom = compile_quality_policy_rom(graph, book)
    addresses = build_recipe_address_vector(graph, rom)
    controller = SequentialAutonomousQualityController(graph, rom, addresses=addresses)
    reader = GraphRecipeReader(graph, addresses)
    a = Commodity("expensive-a", Quality.LEGENDARY)
    b = Commodity("cheap-b", Quality.LEGENDARY)
    stock = {Commodity("iron", Quality.NORMAL): 100}
    demands = {a: 1, b: 1}

    response = None
    terminal = None
    saw_a = False
    saw_b = False
    for _ in range(200):
        decision = controller.step(
            stock=stock,
            demands=demands,
            reader_response=response,
        )
        response = None
        saw_a |= decision.selected_target == a
        saw_b |= decision.selected_target == b
        if decision.kind is SequentialControllerDecisionKind.READ_RECIPE:
            response = reader.read(decision.read_request)
        elif decision.kind is SequentialControllerDecisionKind.DISPATCH:
            terminal = decision
            break
    assert saw_a
    assert saw_b
    assert terminal is not None
    assert terminal.selected_target == b
    assert terminal.intent is not None
    assert terminal.intent.action.recipe_name == "cheap-b"


def test_stock_change_reactivates_previously_exhausted_target() -> None:
    recipes = [ItemRecipe("machine", "machine", 1, {"iron": 2})]
    dag = build_recipe_dag(
        RecipeCatalog(recipes),
        targets=["machine"],
        raw_items={"iron"},
    )
    graph = build_quality_action_graph(dag)
    book = compile_quality_policy_book(graph)
    rom = compile_quality_policy_rom(graph, book)
    addresses = build_recipe_address_vector(graph, rom)
    controller = SequentialAutonomousQualityController(graph, rom, addresses=addresses)
    reader = GraphRecipeReader(graph, addresses)
    target = Commodity("machine", Quality.LEGENDARY)

    blocked, _ = _drive_until_dispatch(
        controller,
        reader,
        stock={},
        demands={target: 1},
    )
    assert blocked.kind is SequentialControllerDecisionKind.BLOCKED

    recovered, _ = _drive_until_dispatch(
        controller,
        reader,
        stock={Commodity("iron", Quality.NORMAL): 10},
        demands={target: 1},
    )
    assert recovered.kind is SequentialControllerDecisionKind.DISPATCH
    assert recovered.intent is not None
    assert recovered.intent.action.recipe_name == "machine"


def test_equal_demand_targets_round_robin_after_accepted_dispatch() -> None:
    _, _, controller, reader, _ = _fixture_two_shared_targets()
    a = Commodity("machine-a", Quality.LEGENDARY)
    b = Commodity("machine-b", Quality.LEGENDARY)
    stock = {Commodity("iron", Quality.NORMAL): 100}
    demands = {a: 1, b: 1}

    first, _ = _drive_until_dispatch(controller, reader, stock=stock, demands=demands)
    assert first.kind is SequentialControllerDecisionKind.DISPATCH
    assert first.intent is not None
    first_target = first.selected_target
    controller.record_dispatch(first.intent)

    second, _ = _drive_until_dispatch(controller, reader, stock=stock, demands=demands)
    assert second.kind is SequentialControllerDecisionKind.DISPATCH
    assert second.selected_target != first_target
