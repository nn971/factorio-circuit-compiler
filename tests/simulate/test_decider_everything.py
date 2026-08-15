from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    InputPort,
    Operand,
    OutputPort,
    PhysicalCircuit,
    SignalId,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.simulate.physical import simulate_stream
from factorio_circuit.target.factorio.signals import SIGNAL_EVERYTHING

IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")
CONTROL = SignalId("virtual", "signal-T")


def test_decider_everything_copies_selected_vector_network() -> None:
    circuit = PhysicalCircuit(
        name="everything_copy",
        entities=[
            ConstantCombinator(1, annotation_only=True),
            ConstantCombinator(2, annotation_only=True),
            DeciderCombinator(
                id=3,
                comparator="!=",
                left=Operand(signal=CONTROL, networks=(WireColor.GREEN,)),
                right=Operand(constant=0),
                output_signal=SIGNAL_EVERYTHING,
                output_copy_count_from_input=True,
                output_networks=(WireColor.RED,),
            ),
            ConstantCombinator(4, annotation_only=True),
        ],
        connections=[
            WireConnection(
                WireEndpoint(1, Connector.SINGLE),
                WireEndpoint(3, Connector.INPUT),
                WireColor.RED,
            ),
            WireConnection(
                WireEndpoint(2, Connector.SINGLE),
                WireEndpoint(3, Connector.INPUT),
                WireColor.GREEN,
            ),
            WireConnection(
                WireEndpoint(3, Connector.OUTPUT),
                WireEndpoint(4, Connector.SINGLE),
                WireColor.RED,
            ),
        ],
        inputs=[
            InputPort("payload", 1, None),
            InputPort("valid", 2, CONTROL),
        ],
        outputs=[OutputPort("out", 4, None, 1)],
    )

    trace = simulate_stream(
        circuit,
        [
            {"payload": {IRON: 11, COPPER: 7}, "valid": 1},
            {"payload": {IRON: 99}, "valid": 0},
        ],
        flush_ticks=1,
    )

    assert trace == [({},), ({IRON: 11, COPPER: 7},), ({},)]
