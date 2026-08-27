from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.multilevel import (
    build_multilevel_hierarchy,
    implementation_hyperedges,
)


def _connection(left: int, right: int, color: WireColor = WireColor.RED) -> WireConnection:
    return WireConnection(
        WireEndpoint(left, Connector.SINGLE),
        WireEndpoint(right, Connector.SINGLE),
        color,
    )


def _circuit(count: int, connections: list[WireConnection]) -> PhysicalCircuit:
    return PhysicalCircuit(
        "multilevel-test",
        entities=[ConstantCombinator(entity_id) for entity_id in range(1, count + 1)],
        connections=connections,
    )


def test_hyperedge_extraction_is_independent_of_pairwise_net_representation() -> None:
    chain = _circuit(4, [_connection(1, 2), _connection(2, 3), _connection(3, 4)])
    star = _circuit(4, [_connection(1, 2), _connection(1, 3), _connection(1, 4)])

    assert implementation_hyperedges(chain) == implementation_hyperedges(star)
    assert implementation_hyperedges(chain)[0].members == (1, 2, 3, 4)


def test_multilevel_coarsening_halves_one_shared_net_deterministically() -> None:
    circuit = _circuit(
        8,
        [_connection(1, entity_id) for entity_id in range(2, 9)],
    )

    hierarchy = build_multilevel_hierarchy(circuit, target_macros=1)

    assert [len(level.macros) for level in hierarchy.levels] == [8, 4, 2, 1]
    assert hierarchy.levels[1].macros[0].members == (1, 2)
    assert hierarchy.levels[-1].macros[0].members == tuple(range(1, 9))


def test_fixed_entities_remain_singleton_macros() -> None:
    circuit = _circuit(
        6,
        [_connection(1, entity_id) for entity_id in range(2, 7)],
    )

    hierarchy = build_multilevel_hierarchy(
        circuit,
        fixed_entities=frozenset({1}),
        target_macros=1,
    )

    assert all(
        any(macro.members == (1,) and macro.fixed for macro in level.macros)
        for level in hierarchy.levels
    )
    assert len(hierarchy.levels[-1].macros) > 1


def test_two_terminal_net_outweighs_one_large_bus_pair() -> None:
    circuit = _circuit(
        5,
        [
            _connection(1, 2, WireColor.RED),
            _connection(1, 2, WireColor.GREEN),
            _connection(1, 3, WireColor.GREEN),
            _connection(1, 4, WireColor.GREEN),
            _connection(1, 5, WireColor.GREEN),
        ],
    )

    hierarchy = build_multilevel_hierarchy(circuit, target_macros=3)

    assert (1, 2) in {macro.members for macro in hierarchy.levels[1].macros}
