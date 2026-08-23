import pytest

from examples.autonomous_mall.worker_pool import build_worker_pool
from factorio_circuit import compile_circuit


@pytest.mark.parametrize("worker_count", [1, 2])
def test_worker_pool_compiles_to_blueprint(worker_count: int) -> None:
    result = compile_circuit(build_worker_pool(worker_count))
    assert result.blueprint_string.startswith("0")
