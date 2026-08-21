import pytest

from factorio_circuit import Circuit, SignalId, simulate_stream_with_oracles

IRON = SignalId("item", "iron-plate")


def _observation_module():
    c = Circuit("observations")
    demand = c.input("demand")
    temperature = c.oracle("temperature")
    stock = c.oracle_signals("stock")
    c.output("combined", demand + temperature + stock.signal(IRON))
    return c.build()


def test_scripted_oracle_trace_keeps_reference_simulation_deterministic() -> None:
    module = _observation_module()

    outputs = simulate_stream_with_oracles(
        module,
        [{"demand": 1}, {"demand": 2}],
        [
            {"temperature": 10, "stock": {IRON: 5}},
            {"temperature": 20, "stock": {IRON: 7}},
        ],
    )

    assert outputs == [(16,), (29,)]


def test_oracle_values_cannot_be_smuggled_through_ordinary_input_rows() -> None:
    module = _observation_module()
    with pytest.raises(ValueError, match="through input_stream"):
        simulate_stream_with_oracles(
            module,
            [{"demand": 1, "temperature": 10}],
            [{"temperature": 10, "stock": {IRON: 5}}],
        )


def test_oracle_trace_requires_exact_declared_coverage() -> None:
    module = _observation_module()
    with pytest.raises(ValueError, match="missing oracle"):
        simulate_stream_with_oracles(module, [{"demand": 1}], [{"temperature": 10}])
    with pytest.raises(ValueError, match="undeclared oracle"):
        simulate_stream_with_oracles(
            module,
            [{"demand": 1}],
            [{"temperature": 10, "stock": {IRON: 5}, "humidity": 3}],
        )
