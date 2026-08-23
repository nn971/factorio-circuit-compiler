import pytest

from examples.autonomous_mall.worker_pool import build_worker_pool
from factorio_circuit import lower_to_abstract_physical
from factorio_circuit.synthesis.open_vector import VectorPhysicalSynthesizer


@pytest.mark.parametrize("worker_count", [1, 2])
def test_worker_pool_net_constraints_are_two_colorable(worker_count: int) -> None:
    lowered = lower_to_abstract_physical(build_worker_pool(worker_count))
    synthesizer = VectorPhysicalSynthesizer(lowered.abstract_physical)
    colors = synthesizer._assign_net_colors()
    assert set(colors) == {net.id for net in lowered.abstract_physical.nets}
