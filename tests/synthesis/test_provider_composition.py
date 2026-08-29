import pytest

from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    NetConflict,
)
from factorio_circuit.ir.physical import WireColor
from factorio_circuit.synthesis.provider_composition import _ProviderVectorSynthesizer


def test_provider_required_colors_cannot_violate_hard_net_conflict() -> None:
    circuit = AbstractPhysicalCircuit(
        "provider_color_conflict",
        nets=[AbstractNet(1, (), ()), AbstractNet(2, (), ())],
        net_conflicts=[NetConflict(1, 2, "must remain on opposite wire colors")],
    )
    synthesizer = _ProviderVectorSynthesizer(
        circuit,
        required_net_colors={1: WireColor.RED, 2: WireColor.RED},
    )

    with pytest.raises(ValueError, match="wire-color requirements conflict"):
        synthesizer._assign_net_colors()
