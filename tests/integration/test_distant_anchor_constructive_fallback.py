from factorio_circuit import AnchoredPlacement, Circuit, ScalarConstantOracleProvider
from factorio_circuit.synthesis.placement import PlacementOptions


def test_annealed_compile_falls_back_constructively_for_distant_anchor() -> None:
    circuit = Circuit("distant_anchor_fallback")
    value = circuit.input("value")
    sensor = circuit.oracle("sensor")
    circuit.output("out", value + sensor)

    anchor = (-12.5, -4.5)
    result = circuit.compile(
        optimize=False,
        placement=PlacementOptions(
            strategy="annealed",
            anchor_io=False,
            iterations=0,
            restarts=1,
        ),
        physical_anchors={"world-sensor": anchor},
        oracle_providers={
            "sensor": ScalarConstantOracleProvider(
                7,
                placement=AnchoredPlacement("world-sensor"),
            )
        },
    )

    anchored = next(
        entity
        for entity in result.physical_circuit.entities
        if entity.description == "ORACLE sensor: constant 7"
    )
    assert result.layout.positions[anchored.id] == anchor
    assert result.layout.relays

    for wire in result.layout.wires:
        source = result.layout.positions[wire.source_entity]
        target = result.layout.positions[wire.target_entity]
        distance = ((source[0] - target[0]) ** 2 + (source[1] - target[1]) ** 2) ** 0.5
        assert distance <= 7.0 + 1e-9
