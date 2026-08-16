from factorio_circuit import Circuit, CompileProgress, compile_circuit
from factorio_circuit.synthesis.placement import PlacementOptions


def test_compile_circuit_reports_phases_and_bounded_routing_progress() -> None:
    circuit = Circuit("progress")
    x = circuit.input("x")
    circuit.output("y", (x + 1) * 2)
    updates: list[CompileProgress] = []

    result = compile_circuit(
        circuit,
        optimize=False,
        placement=PlacementOptions(strategy="net-aware", iterations=0, restarts=1),
        progress=updates.append,
    )

    assert result.blueprint_string.startswith("0")
    phases = [update.phase for update in updates]
    for phase in (
        "frontend",
        "optimization",
        "timing",
        "physical-lowering",
        "synthesis",
        "placement",
        "routing",
        "blueprint",
        "done",
    ):
        assert phase in phases

    routing = [update for update in updates if update.phase == "routing"]
    assert routing
    assert routing[0].completed == 0
    assert routing[0].total is not None
    assert routing[-1].completed == routing[-1].total
    assert routing[-1].fraction == 1.0


def test_compile_progress_fraction_is_clamped_and_optional() -> None:
    assert CompileProgress("phase").fraction is None
    assert CompileProgress("phase", completed=5, total=10).fraction == 0.5
    assert CompileProgress("phase", completed=20, total=10).fraction == 1.0
    assert CompileProgress("phase", completed=-1, total=10).fraction == 0.0
