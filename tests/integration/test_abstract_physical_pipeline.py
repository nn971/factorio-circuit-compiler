from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator
from factorio_circuit.ir.physical import SignalId, WireColor
from factorio_circuit.simulate.compare import assert_same_stream, assert_same_values
from factorio_circuit.simulate.physical import simulate_stream


def _unequal_depth() -> Circuit:
    c = Circuit("abstract_unequal_depth")
    a = c.input("a")
    b = c.input("b")
    x = a + 1
    y = x * 3
    c.output("z", y - b)
    return c


def _three_multiplies() -> Circuit:
    c = Circuit("abstract_three_multiplies")
    a = c.input("a")
    b = c.input("b")
    d = c.input("c")
    c.output("x", a * 2)
    c.output("y", b * 2)
    c.output("z", d * 2)
    return c


def test_stateless_pipeline_reaches_final_layout_and_blueprint() -> None:
    result = compile_circuit(_unequal_depth())

    assert result.abstract_physical.nets
    assert len(result.layout.signal_allocation) == len(result.abstract_physical.signals)
    assert result.physical_circuit.outputs[0].phase == 3
    delay_entities = [
        entity
        for entity in result.abstract_physical.entities
        if getattr(entity, "description", None) == "phase alignment delay"
    ]
    assert len(delay_entities) == 2
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"a": 1, "b": 10},
            {"a": 9, "b": -3},
            {"a": 0, "b": 7},
            {"a": -20, "b": 4},
        ],
    )
    assert "blueprint" in result.blueprint_json
    assert result.blueprint_string.startswith("0")


def test_scalar_marker_descriptions_show_synthesized_signal_identity() -> None:
    result = compile_circuit(_unequal_depth(), optimize=False)

    for port in result.physical_circuit.inputs:
        assert port.signal is not None
        marker = result.physical_circuit.entity_by_id(port.marker_entity)
        description = getattr(marker, "description", "") or ""
        assert f"[{port.signal.name}]" in description
        assert "allocated during physical synthesis" not in description

    output = result.physical_circuit.outputs[0]
    assert output.signal is not None
    marker = result.physical_circuit.entity_by_id(output.marker_entity)
    description = getattr(marker, "description", "") or ""
    assert f"[{output.signal.name}]" in description
    assert f"phase +{output.phase} tick(s)" in description

    blueprint_entities = {
        entity["entity_number"]: entity for entity in result.blueprint_json["blueprint"]["entities"]
    }
    for port in result.physical_circuit.inputs:
        assert port.signal is not None
        assert (
            f"[{port.signal.name}]" in blueprint_entities[port.marker_entity]["player_description"]
        )


def test_each_packing_survives_abstract_physical_synthesis() -> None:
    result = compile_circuit(_three_multiplies())

    packed = [
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator) and entity.output_each
    ]
    assert len(packed) == 1
    assert len(packed[0].left.nets) == 3
    assert result.abstract_physical.combinator_count == 1
    assert result.physical_circuit.combinator_count == 1
    assert all(color is WireColor.RED for _net, color in result.layout.net_colors)
    assert_same_values(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"a": 1, "b": 2, "c": 3},
            {"a": -5, "b": 0, "c": 2**31 - 1},
        ],
    )


IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")
FIXED_A = SignalId("virtual", "signal-A")


def _vector_passthrough() -> Circuit:
    c = Circuit("abstract_vector_passthrough")
    data = c.signals("data")
    c.output("data", data)
    return c


def _vector_extract() -> Circuit:
    c = Circuit("abstract_vector_extract")
    data = c.signals("data")
    c.output("iron_plus_one", data.signal(IRON) + 1)
    return c


def _two_vector_extracts() -> Circuit:
    c = Circuit("abstract_two_vector_extracts")
    left = c.signals("left")
    right = c.signals("right")
    c.output("sum", left.signal(IRON) + right.signal(COPPER))
    return c


def test_vector_input_passthrough_is_runtime_open_net() -> None:
    result = compile_circuit(_vector_passthrough(), optimize=False)

    assert result.abstract_physical.inputs[0].signal is None
    assert result.abstract_physical.outputs[0].signal is None
    dynamic_nets = [net for net in result.abstract_physical.nets if net.carries_dynamic_vector]
    assert len(dynamic_nets) == 1
    assert not dynamic_nets[0].signals
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"data": {IRON: 2, COPPER: 1}},
            {"data": {IRON: -4}},
            {"data": {}},
        ],
    )


def test_vector_signal_read_is_a_direct_fixed_lane_view() -> None:
    result = compile_circuit(_vector_extract(), optimize=False)

    assert not any(
        isinstance(entity, ArithmeticCombinator)
        and (entity.description or "").startswith("extract [")
        for entity in result.abstract_physical.entities
    )
    consumer = next(
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator)
    )
    assert consumer.left.signal == IRON
    assert len(consumer.left.nets) == 1
    source_net = result.abstract_physical.net_by_id(consumer.left.nets[0])
    assert source_net.carries_dynamic_vector
    assert result.physical_circuit.outputs[0].phase == 1
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"data": {IRON: 2, COPPER: 100}},
            {"data": {IRON: -5}},
            {"data": {COPPER: 7}},
        ],
    )


def test_two_vector_inputs_stay_distinct_at_scalar_consumer() -> None:
    result = compile_circuit(_two_vector_extracts(), optimize=False)

    dynamic_nets = [net for net in result.abstract_physical.nets if net.carries_dynamic_vector]
    assert len(dynamic_nets) == 2
    consumer = next(
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator)
    )
    assert consumer.left.signal == IRON
    assert consumer.right.signal == COPPER
    source_nets = {consumer.left.nets[0], consumer.right.nets[0]}
    assert source_nets == {net.id for net in dynamic_nets}
    conflict_pairs = {
        frozenset((conflict.left, conflict.right))
        for conflict in result.abstract_physical.net_conflicts
    }
    assert frozenset(source_nets) in conflict_pairs
    physical_consumer = result.physical_circuit.entity_by_id(consumer.id)
    assert physical_consumer.left.networks != physical_consumer.right.networks
    assert all(len(net.endpoints) == 2 for net in dynamic_nets)
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"left": {IRON: 3, COPPER: 100}, "right": {COPPER: 4, IRON: 200}},
            {"left": {IRON: -5}, "right": {COPPER: 2}},
            {"left": {}, "right": {}},
        ],
    )


def test_fixed_vector_signal_is_reserved_from_compiler_allocation() -> None:
    c = Circuit("abstract_fixed_vector_signal")
    x = c.input("x")
    fixed = c.constant_signals({FIXED_A: 7, IRON: 3})
    c.output("fixed", fixed)
    c.output("x_plus_one", x + 1)

    result = compile_circuit(c, optimize=False)

    assert any(FIXED_A in net.fixed_signals for net in result.abstract_physical.nets)
    assert FIXED_A not in result.layout.allocated_signals.values()
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [{"x": 1}, {"x": -2}],
    )


def test_fresh_vector_output_keeps_logical_sample_phase() -> None:
    c = Circuit("abstract_fresh_vector")
    data = c.signals("data")
    c.step(2)
    c.output("later", data.sample())

    result = compile_circuit(c, optimize=False)

    assert result.physical_circuit.outputs[0].phase == 2
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"data": {IRON: 1}},
            {"data": {IRON: 2}},
            {"data": {IRON: 3}},
            {"data": {IRON: 4}},
        ],
    )


def _accumulator() -> Circuit:
    c = Circuit("abstract_accumulator")
    data = c.signals("data")
    clear = c.input("clear")
    memory = c.accumulator("memory")
    memory.add(data)
    memory.clear(when=clear)
    c.step(1)
    c.output("memory", memory.sample())
    return c


def test_accumulator_feedback_and_controls_stay_abstract_until_synthesis() -> None:
    result = compile_circuit(_accumulator(), optimize=False)

    memory = next(
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "AccumulatorReg memory: vector memory"
    )
    gate = next(
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "AccumulatorReg memory: gate add[0]"
    )

    assert memory.left.each
    assert len(memory.left.nets) == 1
    assert memory.right.signal is not None
    assert len(memory.right.nets) == 1
    assert gate.left.each
    assert len(gate.left.nets) == 1
    assert gate.right.signal is not None
    assert len(gate.right.nets) == 1

    conflict_pairs = {
        frozenset((conflict.left, conflict.right))
        for conflict in result.abstract_physical.net_conflicts
    }
    assert frozenset((memory.left.nets[0], memory.right.nets[0])) in conflict_pairs
    assert frozenset((gate.left.nets[0], gate.right.nets[0])) in conflict_pairs

    physical_memory = result.physical_circuit.entity_by_id(memory.id)
    physical_gate = result.physical_circuit.entity_by_id(gate.id)
    assert physical_memory.left.networks != physical_memory.right.networks
    assert physical_gate.left.networks != physical_gate.right.networks
    assert {physical_memory.left.networks, physical_memory.right.networks} == {
        (WireColor.RED,),
        (WireColor.GREEN,),
    }
    assert {physical_gate.left.networks, physical_gate.right.networks} == {
        (WireColor.RED,),
        (WireColor.GREEN,),
    }

    descriptions = {
        getattr(entity, "description", None) for entity in result.physical_circuit.entities
    }
    assert "AccumulatorReg memory: add[0] enabled" not in descriptions

    assert result.state_timing.registers[0].period == 1
    assert result.physical_circuit.outputs[0].phase == 2
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"data": {IRON: 2, COPPER: 1}, "clear": 0},
            {"data": {IRON: 2, COPPER: 1}, "clear": 0},
            {"data": {IRON: 2, COPPER: 1}, "clear": 0},
            {"data": {}, "clear": 1},
            {"data": {}, "clear": 0},
            {"data": {IRON: 3}, "clear": 0},
            {"data": {}, "clear": 0},
        ],
    )


FIB = SignalId("virtual", "signal-F")


def _freeze() -> Circuit:
    c = Circuit("abstract_freeze")
    data = c.signals("data")
    set_signal = c.input("set_signal")
    memory = c.freeze("memory")
    memory.set(data, when=set_signal)
    c.step(1)
    c.output("memory", memory.sample())
    return c


def test_freeze_feedback_and_pass_hold_controls_stay_abstract_until_synthesis() -> None:
    result = compile_circuit(_freeze(), optimize=False)

    gate = next(
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "FreezeReg memory: input gate"
    )
    memory = next(
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.description == "FreezeReg memory: vector memory"
    )

    conflict_pairs = {
        frozenset((conflict.left, conflict.right))
        for conflict in result.abstract_physical.net_conflicts
    }
    assert frozenset((gate.left.nets[0], gate.right.nets[0])) in conflict_pairs
    assert frozenset((memory.left.nets[0], memory.right.nets[0])) in conflict_pairs

    physical_gate = result.physical_circuit.entity_by_id(gate.id)
    physical_memory = result.physical_circuit.entity_by_id(memory.id)
    assert physical_gate.left.networks != physical_gate.right.networks
    assert physical_memory.left.networks != physical_memory.right.networks
    assert {physical_gate.left.networks, physical_gate.right.networks} == {
        (WireColor.RED,),
        (WireColor.GREEN,),
    }
    assert {physical_memory.left.networks, physical_memory.right.networks} == {
        (WireColor.RED,),
        (WireColor.GREEN,),
    }

    stream = [
        {"data": {IRON: 1}, "set_signal": 1},
        {"data": {IRON: 2}, "set_signal": 1},
        {"data": {IRON: 99}, "set_signal": 0},
        {"data": {IRON: 100}, "set_signal": 0},
        {"data": {IRON: 5, COPPER: 2}, "set_signal": 1},
        {"data": {}, "set_signal": 0},
    ]
    assert result.state_timing.registers[0].period == 1
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)


def _switchable_fibonacci() -> Circuit:
    c = Circuit("abstract_switchable_fibonacci")
    on = c.input("on")
    one = c.constant_signals({FIB: 1})
    a = c.freeze("fib_a")
    b = c.accumulator("fib_b")

    old_a = a.sample()
    old_b = b.sample()
    a.set(old_b, when=on)
    b.add(old_a, when=on)
    b.add(one, when=on)

    c.step(1)
    new_a = a.sample()
    new_b = b.sample()
    c.output("fib", new_b.signal(FIB) - new_a.signal(FIB))
    return c


def test_switchable_fibonacci_runs_through_coupled_abstract_state_networks() -> None:
    result = compile_circuit(_switchable_fibonacci(), optimize=False)
    stream = [
        {"on": 1},
        {"on": 1},
        {"on": 1},
        {"on": 1},
        {"on": 1},
        {"on": 0},
        {"on": 0},
        {"on": 1},
        {"on": 1},
    ]
    assert all(item.period == 1 for item in result.state_timing.registers)
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)

    observations = simulate_stream(result.physical_circuit, stream)
    phase = result.physical_circuit.outputs[0].phase
    values = [observations[index + phase][0] for index in range(len(stream))]
    assert values == [1, 1, 2, 3, 5, 5, 5, 8, 13]


def test_signal_reuse_preserves_two_disconnected_scalar_branches() -> None:
    c = Circuit("abstract_signal_reuse")
    a = c.input("a")
    b = c.input("b")
    c.output("x", a + 1)
    c.output("y", b + 2)

    result = compile_circuit(c, optimize=False)

    assert len(result.abstract_physical.signals) == 4
    assert result.layout.concrete_signal_count == 1
    assert len(result.layout.reused_signal_groups) == 1
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"a": 1, "b": 10},
            {"a": -3, "b": 7},
            {"a": 2**31 - 1, "b": -20},
        ],
    )
