from factorio_circuit.analysis import census_phase_delays, format_phase_delay_census
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    AbstractSignal,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    Endpoint,
    InputPort,
    Operand,
)


def _delay_fixture() -> AbstractPhysicalCircuit:
    circuit = AbstractPhysicalCircuit("phase-delay-census")
    circuit.signals.extend(AbstractSignal(index) for index in range(1, 8))
    circuit.entities.extend(
        (
            ConstantCombinator(1, description="INPUT movement", annotation_only=True),
            ArithmeticCombinator(
                2,
                "+",
                Operand(signal=1, nets=(1,)),
                Operand(constant=0),
                False,
                2,
                "phase alignment delay",
            ),
            ArithmeticCombinator(
                3,
                "+",
                Operand(signal=2, nets=(2,)),
                Operand(constant=0),
                False,
                3,
                "phase alignment delay",
            ),
            ArithmeticCombinator(
                4,
                "+",
                Operand(signal=3, nets=(3,)),
                Operand(constant=0),
                False,
                4,
                "phase alignment delay",
            ),
            ArithmeticCombinator(
                5,
                "+",
                Operand(signal=3, nets=(3,)),
                Operand(constant=0),
                False,
                5,
                "phase alignment delay",
            ),
            DeciderCombinator(
                6,
                "!=",
                Operand(signal=4, nets=(4,)),
                Operand(constant=0),
                6,
                description="FreezeReg body_pos_0: set!=0 -> pass",
            ),
            ArithmeticCombinator(
                7,
                "*",
                Operand(signal=5, nets=(5,)),
                Operand(constant=1),
                False,
                7,
                "ordinary scalar consumer",
            ),
            ConstantCombinator(8, description="INPUT data", annotation_only=True),
            ArithmeticCombinator(
                9,
                "+",
                Operand(each=True, nets=(6,)),
                Operand(constant=0),
                True,
                description="vector phase alignment delay",
            ),
            ArithmeticCombinator(
                10,
                "*",
                Operand(each=True, nets=(7,)),
                Operand(constant=1),
                True,
                description="ordinary vector consumer",
            ),
        )
    )
    circuit.nets.extend(
        (
            AbstractNet(
                1,
                (1,),
                (Endpoint(1, Connector.SINGLE), Endpoint(2, Connector.INPUT)),
                label="input movement",
            ),
            AbstractNet(
                2,
                (2,),
                (Endpoint(2, Connector.OUTPUT), Endpoint(3, Connector.INPUT)),
            ),
            AbstractNet(
                3,
                (3,),
                (
                    Endpoint(3, Connector.OUTPUT),
                    Endpoint(4, Connector.INPUT),
                    Endpoint(5, Connector.INPUT),
                ),
            ),
            AbstractNet(
                4,
                (4,),
                (Endpoint(4, Connector.OUTPUT), Endpoint(6, Connector.INPUT)),
            ),
            AbstractNet(
                5,
                (5,),
                (Endpoint(5, Connector.OUTPUT), Endpoint(7, Connector.INPUT)),
            ),
            AbstractNet(
                6,
                (),
                (Endpoint(8, Connector.SINGLE), Endpoint(9, Connector.INPUT)),
                label="vector input data",
                carries_dynamic_vector=True,
            ),
            AbstractNet(
                7,
                (),
                (Endpoint(9, Connector.OUTPUT), Endpoint(10, Connector.INPUT)),
                carries_dynamic_vector=True,
            ),
        )
    )
    circuit.inputs.extend(
        (
            InputPort("movement", Endpoint(1, Connector.SINGLE), 1),
            InputPort("data", Endpoint(8, Connector.SINGLE), None),
        )
    )
    return circuit


def test_phase_delay_census_reconstructs_shared_trunks_and_context() -> None:
    census = census_phase_delays(_delay_fixture())

    assert census.total_delays == 5
    assert census.scalar_delays == 4
    assert census.vector_delays == 1
    assert census.components == 2
    assert census.linear_components == 1
    assert census.branching_components == 1
    assert census.merging_components == 0
    assert census.mixed_kind_components == 0
    assert census.max_component_size == 4
    assert census.max_depth == 3
    assert dict(census.component_size_histogram) == {"3-4": 1, "1": 1}
    assert dict(census.source_classes) == {"external-input": 5}
    assert dict(census.sink_classes) == {
        "mixed[computation+state]": 4,
        "computation": 1,
    }

    largest = census.component_details[0]
    assert largest.kind == "scalar"
    assert largest.delay_entities == 4
    assert largest.roots == 1
    assert largest.leaves == 2
    assert largest.branch_points == 1
    assert largest.merge_points == 0
    assert largest.sources == ("input:movement",)
    assert "state:FreezeReg.body_pos_*:set!=0 -> pass" in largest.sinks
    assert "computation:ordinary scalar consumer" in largest.sinks

    rendered = format_phase_delay_census(census)
    assert "phase delay deep census" in rendered
    assert "scalar=4" in rendered
    assert "input:movement" in rendered
