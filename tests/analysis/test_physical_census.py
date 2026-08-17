from factorio_circuit.analysis import census_abstract_physical, format_abstract_physical_census
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    AbstractSignal,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    Endpoint,
    NetConflict,
    Operand,
    SignalAlias,
    SignalConflict,
)
from factorio_circuit.ir.physical import SignalId


def _census_fixture() -> AbstractPhysicalCircuit:
    circuit = AbstractPhysicalCircuit("census")
    circuit.signals.extend(AbstractSignal(index) for index in range(1, 5))
    circuit.entities.extend(
        (
            ArithmeticCombinator(
                id=1,
                operation="+",
                left=Operand(signal=1, nets=(1,)),
                right=Operand(constant=0),
                output_each=False,
                output_signal=2,
                description="phase alignment delay",
            ),
            ArithmeticCombinator(
                id=2,
                operation="+",
                left=Operand(each=True, nets=(2,)),
                right=Operand(constant=0),
                output_each=True,
                description="vector phase alignment delay",
            ),
            DeciderCombinator(
                id=3,
                comparator="!=",
                left=Operand(signal=2, nets=(3,)),
                right=Operand(constant=0),
                output_signal=3,
                description="FreezeReg body_pos_0: set!=0 -> pass",
            ),
            ArithmeticCombinator(
                id=4,
                operation="*",
                left=Operand(each=True, nets=(4,)),
                right=Operand(signal=3, nets=(3,)),
                output_each=True,
                description="FreezeReg body_pos_0: vector memory",
            ),
            ConstantCombinator(5, annotation_only=True, description="OUTPUT demo"),
        )
    )
    circuit.nets.extend(
        (
            AbstractNet(1, (1,), (Endpoint(1, Connector.INPUT),)),
            AbstractNet(
                2,
                (),
                (Endpoint(2, Connector.INPUT), Endpoint(1, Connector.OUTPUT)),
                carries_dynamic_vector=True,
            ),
            AbstractNet(
                3,
                (2, 3),
                (
                    Endpoint(3, Connector.INPUT),
                    Endpoint(4, Connector.INPUT),
                    Endpoint(3, Connector.OUTPUT),
                ),
            ),
            AbstractNet(
                4,
                (),
                (Endpoint(4, Connector.OUTPUT), Endpoint(5, Connector.SINGLE)),
                fixed_signals=(SignalId("virtual", "signal-A"),),
                carries_dynamic_vector=True,
            ),
        )
    )
    circuit.signal_conflicts.append(SignalConflict(1, 2, "fixture"))
    circuit.signal_aliases.append(SignalAlias(3, 4, "fixture"))
    circuit.net_conflicts.append(NetConflict(1, 2, "fixture"))
    return circuit


def test_abstract_physical_census_reports_lowering_artifacts_and_net_shape() -> None:
    census = census_abstract_physical(_census_fixture())

    assert census.implementation_entities == 4
    assert census.annotation_entities == 1
    assert census.total_entities == 5
    assert census.phase_delay_entities == 2
    assert census.state_implementation_entities == 2
    assert dict(census.lowering_roles) == {
        "annotation": 1,
        "phase-delay.scalar": 1,
        "phase-delay.vector": 1,
        "state.freeze.memory": 1,
        "state.freeze.pass-control": 1,
    }
    assert dict(census.state_families) == {"FreezeReg.body_pos_*": 2}
    assert dict(census.arithmetic_operations) == {"+": 2, "*": 1}
    assert dict(census.decider_comparators) == {"!=": 1}
    assert census.abstract_signals == 4
    assert census.signal_conflicts == 1
    assert census.signal_aliases == 1
    assert census.abstract_nets == 4
    assert census.net_conflicts == 1
    assert census.dynamic_vector_nets == 2
    assert census.fixed_signal_nets == 1
    assert census.nets_with_abstract_signals == 2
    assert census.max_signals_per_net == 2
    assert dict(census.net_endpoint_histogram) == {"2": 2, "1": 1, "3-4": 1}
    assert census.max_net_endpoints == 3

    rendered = format_abstract_physical_census(census)
    assert "phase_delays=2" in rendered
    assert "FreezeReg.body_pos_*" in rendered
