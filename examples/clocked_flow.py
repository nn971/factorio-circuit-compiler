"""Compact example of the supported clocked-flow data contract."""

from factorio_circuit import Circuit, compile_circuit
from factorio_circuit.ir.output import OutputMaterializationPolicy


def build_clocked_flow_example() -> Circuit:
    circuit = Circuit("clocked_flow")

    enabled = circuit.input("enabled")
    source = circuit.signal_event("source", guaranteed_min_separation=4)
    report = circuit.event("report", guaranteed_min_separation=5)

    gated_report = circuit.gate_clock(
        report,
        when=circuit.sample_on(enabled, report),
    )

    tail = source.step(1)
    latest = circuit.hold_into(source, gated_report)
    window = circuit.sum_into(source, gated_report)

    circuit.output("tail", tail, policy=OutputMaterializationPolicy.VALID)
    circuit.output("latest", latest, policy=OutputMaterializationPolicy.VALID)
    circuit.output("window", window, policy=OutputMaterializationPolicy.ZERO)
    return circuit


if __name__ == "__main__":
    print(compile_circuit(build_clocked_flow_example()).blueprint_string)
