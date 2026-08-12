import pytest

from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    AbstractSignal,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    Endpoint,
    NetConflict,
    Operand,
    SignalConflict,
)


def _minimal_circuit() -> AbstractPhysicalCircuit:
    return AbstractPhysicalCircuit(
        name="abstract",
        signals=[AbstractSignal(1, "x"), AbstractSignal(2, "y")],
        entities=[
            ArithmeticCombinator(
                id=10,
                operation="+",
                left=Operand(signal=1, nets=(100, 101)),
                right=Operand(constant=1),
                output_each=False,
                output_signal=2,
            )
        ],
        nets=[
            AbstractNet(100, (1,), (Endpoint(10, Connector.INPUT),), "source-a"),
            AbstractNet(101, (1,), (Endpoint(10, Connector.INPUT),), "source-b"),
        ],
        signal_conflicts=[SignalConflict(1, 2, "simultaneously visible")],
        net_conflicts=[NetConflict(100, 101, "must remain electrically distinct")],
    )


def test_abstract_signals_are_independent_from_nets() -> None:
    circuit = _minimal_circuit()

    circuit.validate()

    entity = circuit.entity_by_id(10)
    assert isinstance(entity, ArithmeticCombinator)
    assert entity.left.nets == (100, 101)
    assert circuit.net_by_id(100).signals == (1,)
    assert circuit.signal_by_id(1).label == "x"


def test_signal_may_appear_on_multiple_disconnected_nets() -> None:
    circuit = _minimal_circuit()

    circuit.validate()

    assert circuit.net_by_id(100).signals == circuit.net_by_id(101).signals


def test_validation_rejects_unknown_net_signal() -> None:
    circuit = _minimal_circuit()
    circuit.nets[0] = AbstractNet(100, (999,), (Endpoint(10, Connector.INPUT),), "bad")

    with pytest.raises(ValueError, match="unknown signal id 999"):
        circuit.validate()


def test_validation_rejects_self_conflicts() -> None:
    circuit = _minimal_circuit()
    circuit.signal_conflicts = [SignalConflict(1, 1)]

    with pytest.raises(ValueError, match="same signal twice"):
        circuit.validate()


def test_dynamic_operand_requires_explicit_nets() -> None:
    with pytest.raises(ValueError, match="select at least one abstract net"):
        Operand(signal=1)


def test_arithmetic_output_is_exactly_each_or_signal() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ArithmeticCombinator(
            id=1,
            operation="+",
            left=Operand(each=True, nets=(1,)),
            right=Operand(constant=0),
            output_each=True,
            output_signal=1,
        )


def test_net_can_describe_fixed_and_runtime_vector_lanes() -> None:
    from factorio_circuit.ir.physical import SignalId

    fixed = SignalId("item", "iron-plate")
    circuit = _minimal_circuit()
    circuit.nets[0] = AbstractNet(
        100,
        (1,),
        (Endpoint(10, Connector.INPUT),),
        "vector-like",
        fixed_signals=(fixed,),
        carries_dynamic_vector=True,
    )

    circuit.validate()

    assert circuit.net_by_id(100).fixed_signals == (fixed,)
    assert circuit.net_by_id(100).carries_dynamic_vector


def test_fixed_signal_operand_does_not_need_abstract_allocation() -> None:
    from factorio_circuit.ir.physical import SignalId

    fixed = SignalId("item", "iron-plate")
    circuit = _minimal_circuit()
    circuit.entities[0] = ArithmeticCombinator(
        id=10,
        operation="+",
        left=Operand(signal=fixed, nets=(100,)),
        right=Operand(constant=0),
        output_each=False,
        output_signal=2,
    )

    circuit.validate()


def test_signal_producing_constant_cannot_be_electrically_orphaned() -> None:
    circuit = AbstractPhysicalCircuit(
        "orphan_constant",
        signals=[AbstractSignal(1)],
        entities=[
            ConstantCombinator(
                id=1,
                signals=((1, 1),),
                description="dead control",
            )
        ],
        nets=[
            AbstractNet(
                id=1,
                signals=(1,),
                endpoints=(Endpoint(1, Connector.SINGLE),),
            )
        ],
    )

    with pytest.raises(ValueError, match="electrically orphaned"):
        circuit.validate()
