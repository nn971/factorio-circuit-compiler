import pytest

from factorio_circuit import Circuit, CircuitBuildError
from factorio_circuit.ir.oracle import OracleInput, VectorOracleInput
from factorio_circuit.ir.semantic import InputSample, VectorInputSample


def test_scalar_and_vector_oracles_are_distinct_semantic_sources() -> None:
    c = Circuit("oracles")
    temperature = c.oracle("temperature")
    stock = c.oracle_signals("stock")
    c.output("temperature", temperature)
    c.output("stock", stock)

    module = c.build()
    assert isinstance(module.inputs[0], OracleInput)
    assert isinstance(module.vector_inputs[0], VectorOracleInput)
    assert temperature.ir is module.inputs[0]
    assert stock.ir is module.vector_inputs[0]


def test_oracle_sampling_preserves_oracle_source_identity() -> None:
    c = Circuit("oracle_samples")
    temperature = c.oracle("temperature")
    stock = c.oracle_signals("stock")
    c.step(2)
    temperature_later = temperature.sample()
    stock_later = stock.sample()
    c.output("temperature", temperature_later)
    c.output("stock", stock_later)

    assert isinstance(temperature_later.ir, InputSample)
    assert isinstance(temperature_later.ir.source, OracleInput)
    assert temperature_later.ir.offset == 2
    assert isinstance(stock_later.ir, VectorInputSample)
    assert isinstance(stock_later.ir.source, VectorOracleInput)
    assert stock_later.ir.offset == 2


def test_oracles_share_the_circuit_name_namespace_with_inputs() -> None:
    c = Circuit("oracle_names")
    c.input("temperature")
    with pytest.raises(CircuitBuildError, match="already used"):
        c.oracle("temperature")
