from __future__ import annotations

import pytest

from benchmarks.snake.model import ARROW_SIGNALS
from benchmarks.snake.random_model import (
    FOOD_CANDIDATE_INPUT,
    FOOD_CANDIDATE_ORACLE,
    build_random_snake_circuit,
)
from factorio_circuit import (
    Circuit,
    RandomSignalOracleProvider,
    simulate_stream_with_oracles,
)
from factorio_circuit.devices import pixel_signal
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import SelectorCombinator
from factorio_circuit.simulate.physical import simulate_stream as simulate_physical_stream
from factorio_circuit.simulate.semantic import LogicalOutput
from factorio_circuit.synthesis.placement import PlacementOptions


def _movement(**directions: int) -> dict[object, int]:
    return {ARROW_SIGNALS[direction]: value for direction, value in directions.items()}


def _rows(
    movements: list[dict[object, int]],
    candidates: list[dict[object, int]],
) -> list[dict[str, LogicalOutput]]:
    circuit = build_random_snake_circuit(render_framebuffer=False)
    module = circuit.build()
    trace = simulate_stream_with_oracles(
        module,
        [{"movement": movement, "reset": 0} for movement in movements],
        [{FOOD_CANDIDATE_ORACLE: candidate} for candidate in candidates],
    )
    names = tuple(name for name in module.output.names if name is not None)
    assert len(names) == len(module.output.values)
    return [dict(zip(names, row, strict=True)) for row in trace]


@pytest.mark.slow
@pytest.mark.acceptance
def test_scripted_random_food_is_latched_cleared_then_respawned() -> None:
    first_food = {pixel_signal(11, 8): 1}
    second_food = {pixel_signal(5, 5): 1}
    rows = _rows(
        [_movement(E=1), {}, {}, {}],
        [first_food, first_food, first_food, second_food],
    )

    assert (rows[0]["head_x"], rows[0]["head_y"]) == (9, 8)
    assert rows[0]["score"] == 0
    assert rows[0]["food"] == first_food

    assert (rows[1]["head_x"], rows[1]["head_y"]) == (10, 8)
    assert rows[1]["food"] == first_food

    assert (rows[2]["head_x"], rows[2]["head_y"]) == (11, 8)
    assert rows[2]["score"] == 1
    assert rows[2]["length"] == 2
    assert rows[2]["food"] == {}

    assert rows[3]["score"] == 1
    assert rows[3]["food"] == second_food


@pytest.mark.slow
@pytest.mark.acceptance
def test_stale_or_blocked_random_proposal_is_ignored() -> None:
    blocked = {pixel_signal(8, 8): 1}
    valid = {pixel_signal(5, 5): 1}
    rows = _rows([{}, {}], [blocked, valid])

    assert rows[0]["food"] == {}
    assert rows[1]["food"] == valid


@pytest.mark.slow
@pytest.mark.acceptance
def test_random_food_provider_input_is_not_a_public_semantic_output() -> None:
    circuit = build_random_snake_circuit(render_framebuffer=False)
    module = circuit.build()

    assert FOOD_CANDIDATE_ORACLE in {item.name for item in module.vector_inputs}
    assert all(
        not (name or "").startswith("__oracle_provider_input__") for name in module.output.names
    )
    assert "food" in module.output.names
    assert len(module.state_registers) == 10
    assert "food" in {register.name for register in module.state_registers}


def test_random_selector_provider_consumes_hidden_tap_and_serializes_random_mode() -> None:
    circuit = Circuit("random_selector_probe")
    candidates = circuit.signals("candidates")
    choice = circuit.oracle_signals("choice")
    circuit.bind_oracle_input(choice, FOOD_CANDIDATE_INPUT, candidates)
    circuit.output("choice", choice)

    result = circuit.compile(
        optimize=False,
        placement=PlacementOptions(strategy="row", restarts=1),
        oracle_providers={
            "choice": RandomSignalOracleProvider(
                input_name=FOOD_CANDIDATE_INPUT,
                update_interval=1,
            )
        },
    )

    abstract_selectors = [
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, abstract.SelectorCombinator)
    ]
    physical_selectors = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, SelectorCombinator)
    ]
    assert len(abstract_selectors) == 1
    assert len(physical_selectors) == 1
    assert abstract_selectors[0].operation == "random"
    assert physical_selectors[0].operation == "random"

    assert [port.name for port in result.physical_circuit.inputs] == ["candidates"]
    assert [port.name for port in result.physical_circuit.outputs] == ["choice"]
    assert all(
        not port.name.startswith("__oracle_provider_input__")
        for port in result.physical_circuit.outputs
    )

    blueprint = result.blueprint_json["blueprint"]
    assert isinstance(blueprint, dict)
    entities = blueprint["entities"]
    assert isinstance(entities, list)
    selectors = [
        entity
        for entity in entities
        if isinstance(entity, dict) and entity.get("name") == "selector-combinator"
    ]
    assert len(selectors) == 1
    assert selectors[0]["control_behavior"] == {
        "operation": "random",
        "random_update_interval": 1,
    }

    with pytest.raises(ValueError, match=r"does not evaluate selector mode\(s\) random"):
        simulate_physical_stream(result.physical_circuit, [{"candidates": {}}])
