import pytest

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.frontend import Circuit

LANE = SignalId("virtual", "signal-D")


def _independent_domains(*, connect_outputs: bool) -> Circuit:
    circuit = Circuit("independent_clock_domains")
    data = circuit.signals("data")

    fast = circuit.accumulator("fast")
    slow = circuit.freeze("slow")

    old_slow = slow.sample()

    # The accumulator has no state-dependent control, so it can accept one logical transition per
    # physical tick. The freeze recurrence uses old_slow.any(), which requires P=3.
    fast.add(data)
    slow.set(data, when=old_slow.any())

    circuit.step(1)
    new_fast = fast.sample()
    new_slow = slow.sample()

    if connect_outputs:
        circuit.output("mixed", new_fast.signal(LANE) + new_slow.signal(LANE))
    else:
        circuit.output("fast", new_fast)
        circuit.output("slow", new_slow)
    return circuit


def test_independent_state_components_keep_distinct_clock_periods() -> None:
    result = compile_circuit(_independent_domains(connect_outputs=False), optimize=False)
    periods = {item.register.name: item.period for item in result.state_timing.registers}

    assert periods == {"fast": 1, "slow": 3}
    assert len(result.state_timing.domains) == 2
    assert {domain.period for domain in result.state_timing.domains} == {1, 3}
    assert result.state_timing.uniform_period is None


def test_same_index_expression_unifies_clock_domains() -> None:
    result = compile_circuit(_independent_domains(connect_outputs=True), optimize=False)
    timings = {item.register.name: item for item in result.state_timing.registers}

    assert len(result.state_timing.domains) == 1
    assert timings["fast"].clock_domain == timings["slow"].clock_domain
    assert timings["fast"].period == timings["slow"].period == 3
    assert result.state_timing.uniform_period == 3


def test_legacy_backend_rejects_multicycle_domains() -> None:
    from factorio_circuit.compiler_legacy import compile_legacy_circuit

    circuit = Circuit("legacy_multicycle_rejection")
    data = circuit.signals("data")
    memory = circuit.freeze("memory")
    old = memory.sample()
    memory.set(data, when=old.any())
    circuit.step()
    circuit.output("memory", memory.sample())

    with pytest.raises(ValueError, match="does not implement periodic state commits"):
        compile_legacy_circuit(circuit, optimize=False)
