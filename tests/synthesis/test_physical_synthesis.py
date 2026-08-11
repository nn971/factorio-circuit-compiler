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
)
from factorio_circuit.synthesis.physical import synthesize_layout


def test_color_component_flip_prefers_shared_connector_coalescing() -> None:
    circuit = AbstractPhysicalCircuit("coalescing_choice")
    circuit.signals.extend((AbstractSignal(1), AbstractSignal(2), AbstractSignal(3)))
    circuit.entities.extend(
        ConstantCombinator(index, annotation_only=True, description=f"source {index}")
        for index in range(1, 5)
    )
    circuit.entities.append(
        ArithmeticCombinator(
            id=5,
            operation="+",
            left=Operand(signal=1, nets=(2,)),
            right=Operand(signal=2, nets=(3,)),
            output_each=False,
            output_signal=3,
        )
    )

    shared_input = Endpoint(5, Connector.INPUT)
    circuit.nets.extend(
        (
            AbstractNet(1, (), (Endpoint(1, Connector.SINGLE),)),
            AbstractNet(2, (1,), (Endpoint(2, Connector.SINGLE), shared_input)),
            AbstractNet(3, (2,), (Endpoint(3, Connector.SINGLE), shared_input)),
            AbstractNet(4, (), (Endpoint(4, Connector.SINGLE),)),
            AbstractNet(5, (3,), (Endpoint(5, Connector.OUTPUT),)),
        )
    )
    circuit.net_conflicts.extend((NetConflict(1, 2), NetConflict(3, 4)))

    layout = synthesize_layout(circuit)
    colors = layout.assigned_net_colors
    groups = layout.coalesced_net_groups

    assert colors[1] != colors[2]
    assert colors[3] != colors[4]
    assert colors[2] == colors[3]
    assert groups[2] == groups[3]
    assert groups[1] != groups[2]
    assert layout.physical_net_count == 4
