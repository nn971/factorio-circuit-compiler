from examples.walsh_hadamard import build_wht_circuit
from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator
from factorio_circuit.simulate.compare import assert_same_values


def _two_pairwise_sums() -> Circuit:
    circuit = Circuit("two_pairwise_sums")
    a = circuit.input("a")
    b = circuit.input("b")
    c = circuit.input("c")
    d = circuit.input("d")
    circuit.output("ab", a + b)
    circuit.output("cd", c + d)
    return circuit


def test_dynamic_dynamic_additions_pack_into_each_each() -> None:
    result = compile_circuit(_two_pairwise_sums())

    packed = [
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.left.each
        and entity.right.each
        and entity.output_each
    ]
    assert len(packed) == 1
    assert result.abstract_physical.combinator_count == 1
    assert result.physical_circuit.combinator_count == 1
    assert len(result.abstract_physical.signal_aliases) == 2

    allocation = dict(result.layout.signal_allocation)
    for alias in result.abstract_physical.signal_aliases:
        assert allocation[alias.left] == allocation[alias.right]

    physical = result.physical_circuit.entity_by_id(packed[0].id)
    assert physical.left.networks != physical.right.networks
    assert_same_values(
        result.semantic_ir,
        result.physical_circuit,
        [
            {"a": 7, "b": 3, "c": 11, "d": -4},
            {"a": 0, "b": 3, "c": 11, "d": 0},
            {"a": -9, "b": 0, "c": 0, "d": 5},
            {"a": 0, "b": 0, "c": 0, "d": 0},
        ],
    )


def test_wht8_uses_generic_pairwise_batches_and_remains_equivalent() -> None:
    result = compile_circuit(build_wht_circuit(3))

    packed = [
        entity
        for entity in result.abstract_physical.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.left.each
        and entity.right.each
        and entity.output_each
    ]
    assert packed
    assert result.physical_circuit.combinator_count < 24
    assert_same_values(
        result.semantic_ir,
        result.physical_circuit,
        [
            {f"x{index}": index - 4 for index in range(8)},
            {f"x{index}": 0 if index % 2 == 0 else index for index in range(8)},
            {f"x{index}": (-1) ** index * (index + 1) for index in range(8)},
        ],
    )
