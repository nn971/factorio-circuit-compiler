"""Simple deterministic layout for compiler-generated blueprints."""

from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit


def row_positions(circuit: PhysicalCircuit) -> dict[int, tuple[float, float]]:
    """Place input markers left, implementation in a row, and output markers right."""

    input_ids = {port.marker_entity for port in circuit.inputs}
    output_ids = {port.marker_entity for port in circuit.outputs}
    implementation = [
        entity
        for entity in circuit.entities
        if entity.id not in input_ids and entity.id not in output_ids
    ]

    positions: dict[int, tuple[float, float]] = {}
    for index, port in enumerate(circuit.inputs):
        positions[port.marker_entity] = (-4.0, float(index * 2))
    for index, entity in enumerate(implementation):
        positions[entity.id] = (float(index * 2), 0.0)
    right_x = float(max(2, len(implementation) * 2 + 2))
    for index, output_port in enumerate(circuit.outputs):
        positions[output_port.marker_entity] = (right_x, float(index * 2))

    # Defensive fallback for future annotation entities.
    for entity in circuit.entities:
        if entity.id not in positions:
            assert isinstance(entity, ConstantCombinator)
            positions[entity.id] = (0.0, 4.0 + entity.id)
    return positions
