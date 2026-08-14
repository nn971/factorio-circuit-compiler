from factorio_circuit import Circuit, SignalId, compile_abstract_circuit
from factorio_circuit.ir.physical import ArithmeticCombinator, WireColor
from factorio_circuit.simulate.compare import assert_same_stream
from factorio_circuit.simulate.physical import simulate_stream

FIB = SignalId("virtual", "signal-F")
IRON = SignalId("item", "iron-plate")


def test_vector_signal_output_is_a_direct_fixed_lane_view() -> None:
    c = Circuit("direct_vector_lane")
    data = c.signals("data")
    c.output("iron", data.signal(IRON))

    result = compile_abstract_circuit(c, optimize=False)

    assert result.abstract_physical.combinator_count == 0
    assert result.physical_circuit.combinator_count == 0
    assert result.physical_circuit.outputs[0].signal == IRON
    assert result.physical_circuit.outputs[0].phase == 0
    assert not any(
        "extract [" in (getattr(entity, "description", None) or "")
        for entity in result.physical_circuit.entities
    )
    assert_same_stream(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"data": {IRON: 3}},
            {"data": {IRON: -2}},
            {"data": {}},
        ],
    )


def _switchable_fibonacci() -> Circuit:
    c = Circuit("direct_lane_fibonacci")
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


def test_fibonacci_subtracts_fixed_f_lanes_directly_across_red_green() -> None:
    result = compile_abstract_circuit(_switchable_fibonacci(), optimize=False)

    assert not any(
        "extract [" in (getattr(entity, "description", None) or "")
        for entity in result.physical_circuit.entities
    )

    subtracts = [
        entity
        for entity in result.physical_circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.operation == "-"
        and entity.left.signal == FIB
        and entity.right.signal == FIB
    ]
    assert len(subtracts) == 1
    subtract = subtracts[0]
    assert subtract.left.networks != subtract.right.networks
    assert {subtract.left.networks, subtract.right.networks} == {
        (WireColor.RED,),
        (WireColor.GREEN,),
    }

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
    assert_same_stream(result.semantic_ir, result.physical_circuit, stream)
    observations = simulate_stream(result.physical_circuit, stream)
    phase = result.physical_circuit.outputs[0].phase
    values = [observations[index + phase][0] for index in range(len(stream))]
    assert values == [1, 1, 2, 3, 5, 5, 5, 8, 13]
