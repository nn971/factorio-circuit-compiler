from factorio_circuit import (
    AbstractPhysicalLoweringResult,
    Circuit,
    ExternalOracleProvider,
    SamplingPolicy,
    SignalId,
    lower_to_abstract_physical,
)
from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit, ArithmeticCombinator

VALUE = SignalId("virtual", "signal-A")
OTHER = SignalId("virtual", "signal-B")


def _delay_count(circuit: AbstractPhysicalCircuit, description: str) -> int:
    return sum(
        isinstance(entity, ArithmeticCombinator) and entity.description == description
        for entity in circuit.entities
    )


def _vector_fanout_circuit(*, oracle: bool) -> Circuit:
    c = Circuit("sampling_policy_oracle" if oracle else "sampling_policy_input")
    data = c.oracle_signals("data") if oracle else c.signals("data")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    # A real state recurrence creates a multi-tick logical occurrence.  The two cheap external
    # branches are then scheduled close to the state boundary, so BEGINNING_OF_STEP must transport
    # the phase-zero snapshot while ALAP may observe the still-live external net at that later phase.
    deep = memory.sample()
    for _ in range(5):
        deep = deep + one
    memory.add(deep)
    memory.add(data + one)
    memory.add(data * 2)

    c.step(1)
    c.output("value", memory.sample())
    return c


def _lower(
    circuit: Circuit,
    policy: SamplingPolicy,
    *,
    oracle: bool,
) -> AbstractPhysicalLoweringResult:
    providers = {"data": ExternalOracleProvider()} if oracle else None
    return lower_to_abstract_physical(
        circuit,
        optimize=False,
        oracle_providers=providers,
        sampling_policy=policy,
    )


def test_beginning_of_step_remains_compatibility_default() -> None:
    circuit = _vector_fanout_circuit(oracle=False)
    implicit = lower_to_abstract_physical(circuit, optimize=False)
    explicit = _lower(circuit, SamplingPolicy.BEGINNING_OF_STEP, oracle=False)

    assert _delay_count(implicit.abstract_physical, "vector phase alignment delay") > 0
    assert _delay_count(implicit.abstract_physical, "vector phase alignment delay") == _delay_count(
        explicit.abstract_physical,
        "vector phase alignment delay",
    )
    assert len(implicit.abstract_physical.entities) == len(explicit.abstract_physical.entities)


def test_alap_eliminates_external_vector_input_transport_trunk() -> None:
    circuit = _vector_fanout_circuit(oracle=False)
    beginning = _lower(circuit, SamplingPolicy.BEGINNING_OF_STEP, oracle=False)
    alap = _lower(circuit, SamplingPolicy.ALAP, oracle=False)

    removed = _delay_count(beginning.abstract_physical, "vector phase alignment delay")
    assert removed > 0
    assert _delay_count(alap.abstract_physical, "vector phase alignment delay") == 0
    assert len(beginning.abstract_physical.entities) - len(alap.abstract_physical.entities) == removed


def test_alap_applies_equally_to_external_vector_oracles() -> None:
    circuit = _vector_fanout_circuit(oracle=True)
    beginning = _lower(circuit, SamplingPolicy.BEGINNING_OF_STEP, oracle=True)
    alap = _lower(circuit, SamplingPolicy.ALAP, oracle=True)

    removed = _delay_count(beginning.abstract_physical, "vector phase alignment delay")
    assert removed > 0
    assert _delay_count(alap.abstract_physical, "vector phase alignment delay") == 0
    assert len(beginning.abstract_physical.entities) - len(alap.abstract_physical.entities) == removed


def test_alap_resamples_scalar_lane_views_of_external_vectors() -> None:
    c = Circuit("sampling_policy_external_lanes")
    data = c.signals("data")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    deep = memory.sample()
    for _ in range(5):
        deep = deep + one
    memory.add(deep)
    memory.add(one * (data.signal(VALUE) != 0))
    memory.add(one * (data.signal(OTHER) != 0))
    c.step(1)
    c.output("value", memory.sample())

    beginning = lower_to_abstract_physical(
        c,
        optimize=False,
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )
    alap = lower_to_abstract_physical(
        c,
        optimize=False,
        sampling_policy=SamplingPolicy.ALAP,
    )

    removed = _delay_count(beginning.abstract_physical, "vector phase alignment delay")
    assert removed > 0
    assert _delay_count(alap.abstract_physical, "vector phase alignment delay") == 0


def test_alap_eliminates_scalar_external_input_transport() -> None:
    c = Circuit("sampling_policy_scalar_input")
    enabled = c.input("enabled")
    one = c.constant_signals({VALUE: 1})
    memory = c.accumulator("memory")

    deep = memory.sample()
    for _ in range(5):
        deep = deep + one
    memory.add(deep)
    memory.add(one, when=enabled != 0)
    c.step(1)
    c.output("value", memory.sample())

    beginning = lower_to_abstract_physical(
        c,
        optimize=False,
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )
    alap = lower_to_abstract_physical(
        c,
        optimize=False,
        sampling_policy=SamplingPolicy.ALAP,
    )

    removed = _delay_count(beginning.abstract_physical, "phase alignment delay")
    assert removed > 0
    assert _delay_count(alap.abstract_physical, "phase alignment delay") == 0


def test_alap_can_observe_one_external_vector_at_multiple_consumer_phases() -> None:
    c = Circuit("sampling_policy_path_skew")
    data = c.signals("data")
    one = c.constant_signals({VALUE: 1})

    depth1 = data + one
    depth2 = depth1 + one
    use_at_2 = data + depth2
    depth3 = depth2 + one
    depth4 = depth3 + one
    use_at_4 = data + depth4
    c.output("use_at_2", use_at_2)
    c.output("use_at_4", use_at_4)

    beginning = lower_to_abstract_physical(
        c,
        optimize=False,
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )
    alap = lower_to_abstract_physical(
        c,
        optimize=False,
        sampling_policy=SamplingPolicy.ALAP,
    )

    assert _delay_count(beginning.abstract_physical, "vector phase alignment delay") == 4
    assert _delay_count(alap.abstract_physical, "vector phase alignment delay") == 0
