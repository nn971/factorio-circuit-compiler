import pytest

from examples.autonomous_mall.manual_controller import (
    ASSEMBLER_INTERFACE,
    HEAD_INTERFACE,
    RECYCLER_INTERFACE,
    TILE_HEIGHT,
    TILE_WIDTH,
    build_assembler_tile,
    build_blueprint_book,
    build_head_tile,
    build_recycler_tile,
    compile_manual_tiles,
)
from factorio_circuit import compile_module
from factorio_circuit.ir.state import FreezeRegister


def _names(items) -> set[str]:
    return {item.name for item in items}


def _output_names(module) -> set[str]:
    return {name for name in module.output.names if name is not None}


def test_head_tile_semantic_shape() -> None:
    module = build_head_tile().build()

    assert not module.inputs
    assert _names(module.vector_inputs) == {"stock", "control"}
    assert _names(module.state_registers) == {"snapshot"}
    assert all(isinstance(register, FreezeRegister) for register in module.state_registers)
    assert _output_names(module) == {"available_out", "control_out", "frozen"}


@pytest.mark.parametrize(
    ("builder", "vector_inputs", "state_names", "outputs"),
    [
        (
            build_assembler_tile,
            {"available_in", "control_in", "job_request", "job_recipe"},
            {"mode", "seen", "held_request", "held_recipe"},
            {
                "remaining_out",
                "control_out",
                "requester_demand",
                "input_enable",
                "recipe",
                "accepted",
                "busy",
                "waiting_finished",
                "ack_finished",
                "armed",
            },
        ),
        (
            build_recycler_tile,
            {"available_in", "control_in", "job_request"},
            {"mode", "seen", "held_request"},
            {
                "remaining_out",
                "control_out",
                "requester_demand",
                "input_enable",
                "accepted",
                "busy",
                "waiting_finished",
                "ack_finished",
                "armed",
            },
        ),
    ],
)
def test_worker_tile_semantic_shape(builder, vector_inputs, state_names, outputs) -> None:
    module = builder().build()

    assert _names(module.inputs) == {"working", "finished"}
    assert _names(module.vector_inputs) == vector_inputs
    assert _names(module.state_registers) == state_names
    assert all(isinstance(register, FreezeRegister) for register in module.state_registers)
    assert _output_names(module) == outputs


def test_tile_interfaces_use_one_common_grid_and_matching_horizontal_coordinates() -> None:
    assert HEAD_INTERFACE.grid_size == (TILE_WIDTH, TILE_HEIGHT)
    assert ASSEMBLER_INTERFACE.grid_size == (TILE_WIDTH, TILE_HEIGHT)
    assert RECYCLER_INTERFACE.grid_size == (TILE_WIDTH, TILE_HEIGHT)

    assert HEAD_INTERFACE.outputs["available_out"][1] == ASSEMBLER_INTERFACE.inputs[
        "available_in"
    ][1]
    assert HEAD_INTERFACE.outputs["control_out"][1] == ASSEMBLER_INTERFACE.inputs["control_in"][1]
    assert ASSEMBLER_INTERFACE.outputs["remaining_out"][1] == ASSEMBLER_INTERFACE.inputs[
        "available_in"
    ][1]
    assert ASSEMBLER_INTERFACE.outputs["control_out"][1] == ASSEMBLER_INTERFACE.inputs[
        "control_in"
    ][1]


@pytest.mark.slow
@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("builder", "interface"),
    [
        (build_head_tile, HEAD_INTERFACE),
        (build_assembler_tile, ASSEMBLER_INTERFACE),
        (build_recycler_tile, RECYCLER_INTERFACE),
    ],
)
def test_manual_tile_full_compile_acceptance(builder, interface) -> None:
    result = compile_module(builder(), interface)

    assert result.physical_circuit.combinator_count > 0
    assert result.blueprint_string.startswith("0")
    assert result.blueprint_json["blueprint"]["snap-to-grid"] == {
        "x": TILE_WIDTH,
        "y": TILE_HEIGHT,
    }


@pytest.mark.slow
@pytest.mark.acceptance
def test_decorated_tiles_have_red_external_docks_and_fit_grid() -> None:
    for tile in compile_manual_tiles():
        blueprint = tile.blueprint["blueprint"]
        assert blueprint["snap-to-grid"] == {"x": TILE_WIDTH, "y": TILE_HEIGHT}
        entities = blueprint["entities"]
        wires = blueprint["wires"]
        by_id = {entity["entity_number"]: entity for entity in entities}
        docks = [
            entity
            for entity in entities
            if str(entity.get("player_description", "")).startswith("DOCK ")
        ]
        assert docks
        for dock in docks:
            x = float(dock["position"]["x"])
            y = float(dock["position"]["y"])
            assert 0.0 <= x <= TILE_WIDTH
            assert 0.0 <= y <= TILE_HEIGHT
            dock_id = dock["entity_number"]
            incident = [wire for wire in wires if wire[0] == dock_id or wire[2] == dock_id]
            assert incident
            for left, left_connector, right, right_connector in incident:
                if left == dock_id:
                    assert left_connector == 1
                if right == dock_id:
                    assert right_connector == 1
                assert left in by_id
                assert right in by_id


@pytest.mark.slow
@pytest.mark.acceptance
def test_preassembled_controller_merges_two_horizontal_docks_at_every_seam() -> None:
    tiles = compile_manual_tiles()
    head, assembler, recycler = tiles
    book = build_blueprint_book()
    assembled = book["blueprint_book"]["blueprints"][0]["blueprint"]

    head_count = len(head.blueprint["blueprint"]["entities"])
    assembler_count = len(assembler.blueprint["blueprint"]["entities"])
    recycler_count = len(recycler.blueprint["blueprint"]["entities"])
    raw_count = head_count + 4 * assembler_count + recycler_count

    # Five tile seams; each seam shares the available and control dock marker.
    assert len(assembled["entities"]) == raw_count - 10
    assert assembled["label"].endswith("HEAD P0 P1 Q0 Q1 R0")
