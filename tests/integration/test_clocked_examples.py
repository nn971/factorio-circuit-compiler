from collections.abc import Callable

import pytest

from examples._clocked_harness import DriverSchedule, build_driver_blueprint
from examples.clock_crossings import CASES as CROSSING_CASES
from examples.derived_clocks import CASES as DERIVED_CASES
from examples.event_basics import CASES as BASIC_CASES
from examples.event_state import PERIOD as STATE_PERIOD
from examples.event_state import SCHEDULE as STATE_SCHEDULE
from examples.event_state import build_event_accumulator
from examples.multi_rate_ledger import PERIOD as LEDGER_PERIOD
from examples.multi_rate_ledger import SCHEDULE as LEDGER_SCHEDULE
from examples.multi_rate_ledger import build_multi_rate_ledger
from factorio_circuit import Circuit, compile_circuit

Scenario = tuple[Callable[[], Circuit], DriverSchedule, int]

SCENARIOS: list[Scenario] = [
    *[(case[0], case[1], case[2]) for case in BASIC_CASES.values()],
    *[(case[0], case[1], case[2]) for case in DERIVED_CASES.values()],
    *[(case[0], case[1], case[2]) for case in CROSSING_CASES.values()],
    (build_event_accumulator, STATE_SCHEDULE, STATE_PERIOD),
    (build_multi_rate_ledger, LEDGER_SCHEDULE, LEDGER_PERIOD),
]


@pytest.mark.parametrize(("builder", "schedule", "period"), SCENARIOS)
def test_clocked_examples_compile_with_self_driving_ingame_harness(
    builder: Callable[[], Circuit],
    schedule: DriverSchedule,
    period: int,
) -> None:
    compiled = compile_circuit(builder())
    driver = build_driver_blueprint(
        compiled,
        schedule,
        period=period,
        label="test driver",
    )

    assert compiled.blueprint_string.startswith("0")
    assert driver["blueprint"]["entities"]
    assert driver["blueprint"]["wires"]

    input_names = {port.name for port in compiled.physical_circuit.inputs}
    assert set(schedule) <= input_names

    outputs = {port.name: port for port in compiled.physical_circuit.outputs}
    for name, valid in outputs.items():
        if not name.endswith("__valid"):
            continue
        payload_name = name.removesuffix("__valid")
        assert payload_name in outputs
        assert outputs[payload_name].phase == valid.phase
