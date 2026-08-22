import pytest

from examples.autonomous_mall.anchored_assembler_worker import (
    build_anchored_assembler_worker,
    build_assembler_worker_probe,
    build_worker_with_device,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.state import FreezeRegister


def _names(items) -> set[str]:
    return {item.name for item in items}


def _output_names(module) -> set[str]:
    return {name for name in module.output.names if name is not None}


def test_new_worker_semantic_contract_uses_only_generic_device_observations() -> None:
    module = build_anchored_assembler_worker().build()
    assert _names(module.inputs) == {"working"}
    assert _names(module.vector_inputs) == {
        "available_in",
        "control_in",
        "job_recipe",
        "ingredients",
        "requester_contents",
    }
    assert _names(module.state_registers) == {
        "mode",
        "seen",
        "held_request",
        "held_recipe",
        "started",
    }
    assert all(isinstance(register, FreezeRegister) for register in module.state_registers)
    assert _output_names(module) == {
        "remaining_out",
        "control_out",
        "recipe",
        "requester_demand",
        "enable",
        "accepted",
        "busy",
        "filling",
        "running",
        "armed",
    }


def test_device_socket_layout_is_derived_from_compiled_geometry() -> None:
    # This is an acceptance property rather than a fixed-coordinate contract: the implementation
    # deliberately chooses its socket x-position after physical synthesis.
    from examples.autonomous_mall.anchored_assembler_worker import (
        DEVICE_CLEARANCE,
        INITIAL_SOCKET_X,
        SOCKET_CORRIDOR,
    )

    assert INITIAL_SOCKET_X >= 48.0
    assert SOCKET_CORRIDOR > 0
    assert DEVICE_CLEARANCE > SOCKET_CORRIDOR


@pytest.mark.slow
@pytest.mark.acceptance
def test_worker_and_device_compile_and_compose_without_legacy_adapter() -> None:
    component = build_worker_with_device(modules=("productivity-module-3",) * 4)
    names = [entity.get("name") for entity in component.blueprint["entities"]]
    assert names.count("assembling-machine-3") == 1
    assert names.count("requester-chest") == 1
    assert names.count("active-provider-chest") == 1
    descriptions = [
        str(entity.get("player_description", ""))
        for entity in component.blueprint["entities"]
    ]
    assert not any("MALL ADAPTER" in description for description in descriptions)
    assert {"provider_contents", "finished"} <= {anchor.name for anchor in component.anchors}

@pytest.mark.slow
@pytest.mark.acceptance
def test_worker_probe_materializes_editable_control_and_active_provider() -> None:
    blueprint = build_assembler_worker_probe()
    descriptions = [str(entity.get("player_description", "")) for entity in blueprint["entities"]]
    assert any("PROBE CONTROL D/L — EDIT HERE" in description for description in descriptions)
    names = [entity.get("name") for entity in blueprint["entities"]]
    assert names.count("active-provider-chest") == 1


def test_worker_semantic_transaction_matches_oracle_protocol() -> None:
    """Exercise reservation, FILL -> RUN -> done, and the one-launch-per-D-cycle guard."""
    from factorio_circuit.simulate.semantic import simulate_stream
    from examples.autonomous_mall.anchored_assembler_worker import (
        IRON_GEAR,
        IRON_PLATE,
        MALL_DISPATCH,
        MALL_LAUNCH,
    )

    module = build_anchored_assembler_worker().build()
    names = list(module.output.names)
    index = {name: i for i, name in enumerate(names)}

    def row(*, d: int, l: int, requester: int = 0, working: int = 0) -> dict[str, object]:
        return {
            "available_in": {IRON_PLATE: 100},
            "control_in": {MALL_DISPATCH: d, MALL_LAUNCH: l},
            "job_recipe": {IRON_GEAR: 1},
            "ingredients": {IRON_PLATE: 2},
            "requester_contents": ({IRON_PLATE: requester} if requester else {}),
            "working": working,
        }

    phases = {
        "idle": [row(d=0, l=0)] * 8,
        "reserved": [row(d=1, l=0)] * 8,
        "fill": [row(d=1, l=1)] * 32,
        "loaded": [row(d=1, l=1, requester=2)] * 32,
        "working": [row(d=1, l=1, working=1)] * 32,
        "held_after_done": [row(d=1, l=1)] * 32,
        "rearm": [row(d=0, l=0)] * 16,
    }
    stream = [item for phase in phases.values() for item in phase]
    trace = simulate_stream(module, stream)

    offsets: dict[str, slice] = {}
    cursor = 0
    for name, phase in phases.items():
        offsets[name] = slice(cursor, cursor + len(phase))
        cursor += len(phase)

    def scalar(name: str) -> list[int]:
        return [int(entry[index[name]]) for entry in trace]

    def vector_lane(name: str, signal: SignalId) -> list[int]:
        values: list[int] = []
        for entry in trace:
            value = entry[index[name]]
            assert isinstance(value, dict)
            values.append(int(value.get(signal, 0)))
        return values

    accepted = scalar("accepted")
    remaining = vector_lane("remaining_out", IRON_PLATE)
    demands = vector_lane("requester_demand", IRON_PLATE)
    enables = scalar("enable")
    armed = scalar("armed")

    reserved = offsets["reserved"]
    assert any(value != 0 for value in accepted[reserved])
    assert any(value == 98 for value in remaining[reserved])

    fill = offsets["fill"]
    assert any(value == 2 for value in demands[fill])

    loaded = offsets["loaded"]
    assert any(value != 0 for value in enables[loaded])
    assert any(value == 0 for value in demands[loaded])

    working_phase = offsets["working"]
    assert any(value != 0 for value in enables[working_phase])

    held = offsets["held_after_done"]
    held_tail = slice(max(held.start or 0, (held.stop or 0) - 12), held.stop)
    assert all(value == 0 for value in demands[held_tail])
    assert all(value == 0 for value in enables[held_tail])

    rearm = offsets["rearm"]
    assert any(value != 0 for value in armed[rearm])
