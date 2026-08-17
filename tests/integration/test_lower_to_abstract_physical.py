from factorio_circuit import Circuit, CompileProgress, lower_to_abstract_physical


def test_lower_to_abstract_physical_stops_before_synthesis() -> None:
    circuit = Circuit("lower_only")
    x = circuit.input("x")
    circuit.output("y", (x + 1) * 2)
    updates: list[CompileProgress] = []

    result = lower_to_abstract_physical(circuit, optimize=False, progress=updates.append)

    assert result.abstract_physical.combinator_count == 2
    assert result.abstract_physical.outputs[0].phase == 2
    assert result.clocked is False
    phases = [update.phase for update in updates]
    assert phases == ["frontend", "optimization", "timing", "physical-lowering"]
    assert "synthesis" not in phases
    assert "placement" not in phases
    assert "routing" not in phases
