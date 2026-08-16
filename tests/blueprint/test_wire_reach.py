from math import hypot

from factorio_circuit.blueprint.routing import (
    DEFAULT_SAFE_WIRE_SPAN,
    _relay_overlaps_forbidden,
    route_wires,
    routed_positions,
    validate_entity_clearance,
    validate_wire_spans,
)
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    Operand,
    PhysicalCircuit,
    SignalId,
    WireConnection,
    WireEndpoint,
)


def _long_wire_circuit() -> PhysicalCircuit:
    signal_a = SignalId("virtual", "signal-A")
    left = ArithmeticCombinator(
        id=1,
        operation="+",
        left=Operand(signal=signal_a),
        right=Operand(constant=0),
        output_each=False,
        output_signal=signal_a,
    )
    right = ArithmeticCombinator(
        id=2,
        operation="+",
        left=Operand(signal=signal_a),
        right=Operand(constant=0),
        output_each=False,
        output_signal=signal_a,
    )
    return PhysicalCircuit(
        name="long-wire",
        entities=[left, right],
        connections=[
            WireConnection(
                WireEndpoint(1, Connector.OUTPUT),
                WireEndpoint(2, Connector.INPUT),
            )
        ],
    )


def test_long_wire_is_split_into_reach_safe_segments() -> None:
    circuit = _long_wire_circuit()
    positions = {1: (0.0, 0.0), 2: (30.0, 0.0)}
    plan = route_wires(circuit, positions)
    all_positions = routed_positions(circuit, positions, plan)

    assert plan.relays
    assert len(plan.wires) == len(plan.relays) + 1
    validate_wire_spans(plan.wires, all_positions)

    for wire in plan.wires:
        left = all_positions[wire.source_entity]
        right = all_positions[wire.target_entity]
        assert hypot(left[0] - right[0], left[1] - right[1]) <= DEFAULT_SAFE_WIRE_SPAN + 1e-9


def test_short_wire_needs_no_relay() -> None:
    circuit = _long_wire_circuit()
    positions = {1: (0.0, 0.0), 2: (4.0, 0.0)}
    plan = route_wires(circuit, positions)

    assert plan.relays == ()
    assert len(plan.wires) == 1


def test_branching_style_parallel_routes_do_not_overlap_entities() -> None:
    from factorio_circuit import Circuit, compile_circuit
    from factorio_circuit.synthesis.placement import row_positions

    module = Circuit("controller")
    a = module.input("a")
    b = module.input("b")
    limit = module.input("limit")
    total = (a + b) * 3
    result = (total > limit).select(limit, total)
    module.output("result", result)

    compiled = compile_circuit(module)
    circuit = compiled.physical_circuit
    positions = row_positions(circuit)
    plan = route_wires(circuit, positions)

    validate_entity_clearance(circuit, positions, plan)
    all_positions = routed_positions(circuit, positions, plan)
    validate_wire_spans(plan.wires, all_positions)

    # Regression: the old router placed relays at (6, 0) and near (8, 0), overlapping
    # real combinators and causing Factorio to drop the delayed total path on import.
    real_positions = set(positions.values())
    assert all(relay.position not in real_positions for relay in plan.relays)


def test_router_keeps_relay_entities_out_of_reserved_areas() -> None:
    circuit = _long_wire_circuit()
    positions = {1: (0.0, 0.0), 2: (30.0, 0.0)}
    reserved = ((10.0, 12.0, -2.0, 2.0),)

    plan = route_wires(circuit, positions, relay_forbidden_areas=reserved)

    assert plan.relays
    assert all(not _relay_overlaps_forbidden(relay.position, reserved) for relay in plan.relays)
