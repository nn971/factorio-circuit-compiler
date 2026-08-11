from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.ir.physical import ArithmeticCombinator, DeciderCombinator, SignalId, WireColor
from factorio_circuit.simulate.compare import assert_same_stream
from factorio_circuit.simulate.physical import simulate_stream

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def _accumulator() -> Circuit:
    c = Circuit("accumulator")
    data = c.signals("data")
    clear = c.input("clear")
    memory = c.accumulator("memory")
    memory.add(data)
    memory.clear(when=clear)
    c.tick(1)
    c.output("memory", memory.value)
    return c


def _freeze() -> Circuit:
    c = Circuit("freeze")
    data = c.signals("data")
    set_signal = c.input("set_signal")
    memory = c.freeze("memory")
    memory.set(data, when=set_signal)
    c.tick(1)
    c.output("memory", memory.value)
    return c


def test_accumulator_uses_vector_memory_cell() -> None:
    result = compile_circuit(_accumulator(), optimize=False)
    memories = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "AccumulatorReg memory: vector memory"
    ]
    assert len(memories) == 1
    memory = memories[0]
    assert memory.left.each
    assert memory.left.networks == (WireColor.RED,)
    assert memory.right.signal == SignalId("virtual", "signal-green")
    assert memory.right.networks == (WireColor.GREEN,)
    assert memory.output_each

    descriptions = {
        getattr(entity, "description", None)
        for entity in result.physical_circuit.entities
    }
    assert "AccumulatorReg memory: add[0] enabled" not in descriptions


def test_accumulator_blueprint_exposes_vector_io() -> None:
    result = compile_circuit(_accumulator(), optimize=False)
    descriptions = [
        item.get("player_description", "")
        for item in result.blueprint_json["blueprint"]["entities"]
    ]
    assert any("INPUT data" in item and "whole signal vector" in item for item in descriptions)
    assert any("OUTPUT memory" in item and "whole signal vector" in item for item in descriptions)


def test_freeze_has_pass_and_hold_controls() -> None:
    result = compile_circuit(_freeze(), optimize=False)
    descriptions = [
        getattr(entity, "description", "") or "" for entity in result.physical_circuit.entities
    ]
    assert any("set!=0 -> pass" in item for item in descriptions)
    assert any("set=0 -> hold" in item for item in descriptions)
    assert any("transparent input gate" in item for item in descriptions)
    assert any("vector memory" in item for item in descriptions)
    assert (
        sum(
            isinstance(entity, DeciderCombinator)
            for entity in result.physical_circuit.entities
        )
        >= 2
    )



def test_accumulator_matches_reference_state_stream() -> None:
    result = compile_circuit(_accumulator(), optimize=False)
    stream: list[dict[str, object]] = [
        {"data": {IRON: 2, COPPER: 1}, "clear": 0},
        {"data": {IRON: 2, COPPER: 1}, "clear": 0},
        {"data": {IRON: 2, COPPER: 1}, "clear": 0},
        {"data": {}, "clear": 1},
        {"data": {}, "clear": 0},
        {"data": {IRON: 3}, "clear": 0},
        {"data": {}, "clear": 0},
    ]
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)


def test_freeze_matches_reference_state_stream() -> None:
    result = compile_circuit(_freeze(), optimize=False)
    stream: list[dict[str, object]] = [
        {"data": {IRON: 1}, "set_signal": 1},
        {"data": {IRON: 2}, "set_signal": 1},
        {"data": {IRON: 99}, "set_signal": 0},
        {"data": {IRON: 100}, "set_signal": 0},
        {"data": {IRON: 5}, "set_signal": 1},
        {"data": {}, "set_signal": 0},
    ]
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)

def test_accumulator_accumulates_and_clears_in_physical_simulator() -> None:
    result = compile_circuit(_accumulator(), optimize=False)
    stream = [
        {"data": {IRON: 2, COPPER: 1}, "clear": 0},
        {"data": {IRON: 2, COPPER: 1}, "clear": 0},
        {"data": {IRON: 2, COPPER: 1}, "clear": 0},
        {"data": {IRON: 0}, "clear": 1},
        {"data": {}, "clear": 1},
        {"data": {}, "clear": 0},
        {"data": {IRON: 3}, "clear": 0},
        {"data": {IRON: 3}, "clear": 0},
        {"data": {}, "clear": 0},
        {"data": {}, "clear": 0},
    ]
    observations = simulate_stream(result.physical_circuit, stream, flush_ticks=4)
    vector_values = [row[0] for row in observations]
    assert any(isinstance(value, dict) and value.get(IRON, 0) >= 4 for value in vector_values)
    assert {} in vector_values[4:9]


def test_freeze_passes_then_holds_last_vector() -> None:
    result = compile_circuit(_freeze(), optimize=False)
    stream = [
        {"data": {IRON: 1}, "set_signal": 1},
        {"data": {IRON: 2}, "set_signal": 1},
        {"data": {IRON: 3}, "set_signal": 1},
        {"data": {IRON: 99}, "set_signal": 0},
        {"data": {IRON: 100}, "set_signal": 0},
        {"data": {IRON: 101}, "set_signal": 0},
        {"data": {IRON: 102}, "set_signal": 0},
    ]
    observations = simulate_stream(result.physical_circuit, stream, flush_ticks=5)
    values = [row[0] for row in observations]
    tail = [value for value in values[-4:] if isinstance(value, dict)]
    assert tail
    assert all(value.get(IRON, 0) == tail[0].get(IRON, 0) for value in tail)
    assert tail[0].get(IRON, 0) not in {99, 100, 101, 102}
