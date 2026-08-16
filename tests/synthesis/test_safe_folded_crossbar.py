from __future__ import annotations

from itertools import combinations

from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ConstantCombinator,
    InputPort,
    OutputPort,
    PhysicalCircuit,
)
from factorio_circuit.synthesis import open_vector
from factorio_circuit.synthesis.open_vector import synthesize_vector_layout
from factorio_circuit.synthesis.safe_crossbar import safe_crossbar_options
from factorio_circuit.synthesis.safe_folded_crossbar import (
    _folded_ordered_entities,
    safe_folded_crossbar_options,
)


def _long_fixture(entity_count: int = 64) -> abstract.AbstractPhysicalCircuit:
    circuit = abstract.AbstractPhysicalCircuit("safe_folded_crossbar")
    circuit.entities.extend(
        abstract.ConstantCombinator(entity_id, annotation_only=True)
        for entity_id in range(1, entity_count + 1)
    )
    single = abstract.Connector.SINGLE
    circuit.nets.append(
        abstract.AbstractNet(
            1,
            (),
            (abstract.Endpoint(1, single), abstract.Endpoint(entity_count, single)),
        )
    )
    return circuit


def test_folded_safe_crossbar_crosses_rows_without_router_search(monkeypatch) -> None:
    def fail_router(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("heuristic route_wires must not run for safe-folded-crossbar")

    monkeypatch.setattr(open_vector, "route_wires", fail_router)
    layout = synthesize_vector_layout(
        _long_fixture(),
        safe_wire_span=7.0,
        placement=safe_folded_crossbar_options(),
    )

    real_y = {layout.positions[entity.id][1] for entity in layout.circuit.entities}
    assert len(real_y) > 1
    assert any("fold stitch" in relay.description for relay in layout.relays)

    for left, right in combinations(layout.relays, 2):
        dx = abs(left.position[0] - right.position[0])
        dy = abs(left.position[1] - right.position[1])
        assert dx >= 1.1 or dy >= 1.1


def test_folded_safe_crossbar_is_deterministic() -> None:
    first = synthesize_vector_layout(
        _long_fixture(),
        safe_wire_span=7.0,
        placement=safe_folded_crossbar_options(),
    )
    second = synthesize_vector_layout(
        _long_fixture(),
        safe_wire_span=7.0,
        placement=safe_folded_crossbar_options(),
    )

    assert first.positions == second.positions
    assert first.relays == second.relays
    assert first.wires == second.wires


def test_folded_and_linear_safe_strategies_remain_independent() -> None:
    folded = safe_folded_crossbar_options()
    linear = safe_crossbar_options()

    assert str(folded.strategy) == "safe-folded-crossbar"
    assert str(linear.strategy) == "safe-crossbar"


def test_folded_order_places_public_inputs_and_outputs_together_first() -> None:
    circuit = PhysicalCircuit("front_panel")
    circuit.entities.extend(ConstantCombinator(entity_id) for entity_id in range(1, 7))
    circuit.inputs.append(InputPort("movement", marker_entity=5, signal=None))
    circuit.outputs.append(OutputPort("framebuffer", marker_entity=6, signal=None, phase=0))

    ordered = _folded_ordered_entities(circuit)

    assert [entity.id for entity in ordered[:2]] == [5, 6]
    assert {entity.id for entity in ordered[2:]} == {1, 2, 3, 4}
