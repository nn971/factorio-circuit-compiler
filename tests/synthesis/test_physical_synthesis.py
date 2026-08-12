import pytest

from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    AbstractSignal,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    Endpoint,
    InputPort,
    NetConflict,
    Operand,
    SignalConflict,
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



def test_signal_allocator_reuses_identity_on_disconnected_physical_groups() -> None:
    circuit = AbstractPhysicalCircuit("signal_reuse")
    circuit.signals.extend((AbstractSignal(1), AbstractSignal(2)))
    circuit.entities.extend(
        (
            ConstantCombinator(1, annotation_only=True, description="input a"),
            ConstantCombinator(2, annotation_only=True, description="input b"),
        )
    )
    circuit.nets.extend(
        (
            AbstractNet(1, (1,), (Endpoint(1, Connector.SINGLE),)),
            AbstractNet(2, (2,), (Endpoint(2, Connector.SINGLE),)),
        )
    )
    circuit.inputs.extend(
        (
            InputPort("a", Endpoint(1, Connector.SINGLE), 1),
            InputPort("b", Endpoint(2, Connector.SINGLE), 2),
        )
    )

    layout = synthesize_layout(circuit)

    assert layout.allocated_signals[1] == layout.allocated_signals[2]
    assert layout.concrete_signal_count == 1
    assert layout.signal_slots_saved == 1
    assert tuple(layout.reused_signal_groups.values()) == ((1, 2),)


def test_signal_allocator_keeps_lanes_distinct_on_same_physical_group() -> None:
    circuit = AbstractPhysicalCircuit("same_group_interference")
    circuit.signals.extend((AbstractSignal(1), AbstractSignal(2), AbstractSignal(3)))
    circuit.entities.extend(
        (
            ConstantCombinator(1, annotation_only=True),
            ConstantCombinator(2, annotation_only=True),
            ArithmeticCombinator(
                id=3,
                operation="+",
                left=Operand(signal=1, nets=(1,)),
                right=Operand(signal=2, nets=(2,)),
                output_each=False,
                output_signal=3,
            ),
        )
    )
    shared = Endpoint(3, Connector.INPUT)
    circuit.nets.extend(
        (
            AbstractNet(1, (1,), (Endpoint(1, Connector.SINGLE), shared)),
            AbstractNet(2, (2,), (Endpoint(2, Connector.SINGLE), shared)),
            AbstractNet(3, (3,), (Endpoint(3, Connector.OUTPUT),)),
        )
    )

    layout = synthesize_layout(circuit)

    assert layout.coalesced_net_groups[1] == layout.coalesced_net_groups[2]
    assert layout.allocated_signals[1] != layout.allocated_signals[2]


def test_signal_allocator_honors_explicit_conflict_across_disconnected_nets() -> None:
    circuit = AbstractPhysicalCircuit("signal_conflict")
    circuit.signals.extend((AbstractSignal(1), AbstractSignal(2)))
    circuit.entities.extend(
        (
            ConstantCombinator(1, annotation_only=True),
            ConstantCombinator(2, annotation_only=True),
        )
    )
    circuit.nets.extend(
        (
            AbstractNet(1, (1,), (Endpoint(1, Connector.SINGLE),)),
            AbstractNet(2, (2,), (Endpoint(2, Connector.SINGLE),)),
        )
    )
    circuit.signal_conflicts.append(SignalConflict(1, 2, "must stay distinct"))

    layout = synthesize_layout(circuit)

    assert layout.allocated_signals[1] != layout.allocated_signals[2]


def test_transitive_net_coalescing_does_not_merge_repeated_lane() -> None:
    circuit = AbstractPhysicalCircuit("transitive_merge_guard")
    circuit.signals.extend((AbstractSignal(1), AbstractSignal(2)))
    circuit.entities.extend(
        ConstantCombinator(index, annotation_only=True)
        for index in range(1, 6)
    )
    left_join = Endpoint(4, Connector.SINGLE)
    right_join = Endpoint(5, Connector.SINGLE)
    circuit.nets.extend(
        (
            AbstractNet(1, (1,), (Endpoint(1, Connector.SINGLE), left_join)),
            AbstractNet(
                2,
                (2,),
                (Endpoint(2, Connector.SINGLE), left_join, right_join),
            ),
            AbstractNet(3, (1,), (Endpoint(3, Connector.SINGLE), right_join)),
        )
    )

    layout = synthesize_layout(circuit)
    groups = layout.coalesced_net_groups

    assert groups[1] != groups[3]
    assert layout.assigned_net_colors[1] != layout.assigned_net_colors[3]



def test_runtime_open_vector_net_never_coalesces_with_another_net() -> None:
    circuit = AbstractPhysicalCircuit("dynamic_vector_isolation")
    circuit.signals.append(AbstractSignal(1))
    circuit.entities.extend(
        ConstantCombinator(index, annotation_only=True)
        for index in range(1, 4)
    )
    shared = Endpoint(3, Connector.SINGLE)
    circuit.nets.extend(
        (
            AbstractNet(
                1,
                (),
                (Endpoint(1, Connector.SINGLE), shared),
                carries_dynamic_vector=True,
            ),
            AbstractNet(2, (1,), (Endpoint(2, Connector.SINGLE), shared)),
        )
    )

    layout = synthesize_layout(circuit)

    assert layout.assigned_net_colors[1] != layout.assigned_net_colors[2]
    assert layout.coalesced_net_groups[1] != layout.coalesced_net_groups[2]



def test_runtime_open_vector_net_rejects_compiler_allocated_lane() -> None:
    circuit = AbstractPhysicalCircuit("dynamic_vector_compiler_lane")
    circuit.signals.append(AbstractSignal(1))
    circuit.entities.append(ConstantCombinator(1, annotation_only=True))
    circuit.nets.append(
        AbstractNet(
            1,
            (1,),
            (Endpoint(1, Connector.SINGLE),),
            carries_dynamic_vector=True,
        )
    )

    with pytest.raises(ValueError, match="runtime-open vector net"):
        synthesize_layout(circuit)
