"""Opt-in scalability/quality corpus for the generic physical-layout optimizer.

The cases are deliberately synthetic and unrelated to compiler construction strategies. They feed
complete hand-built routed :class:`Layout` objects through the same public API used by the
safe-folded Snake benchmark.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from statistics import median
from time import monotonic

from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    InputPort,
    Operand,
    OutputPort,
    PhysicalCircuit,
    SignalId,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LayoutOptimizationResult,
    LegalPlacementLattice,
    optimize_physical_layout,
    validate_physical_layout,
)
from factorio_circuit.synthesis.placement import PlacementOptions, RelayForbiddenArea


@dataclass(frozen=True, slots=True)
class _CorpusCase:
    name: str
    problem: LayoutOptimizationProblem


def _rectangular_lattice(
    width: int,
    height: int,
    *,
    y_offset: int = 0,
    include_wide: bool = False,
    forbidden_areas: tuple[RelayForbiddenArea, ...] = (),
) -> LegalPlacementLattice:
    unit_sites = tuple(
        (float(x), float(y)) for y in range(y_offset, y_offset + height) for x in range(width)
    )
    wide_sites = (
        tuple(
            (float(x), float(y))
            for y in range(y_offset, y_offset + height)
            for x in range(1, width - 1)
        )
        if include_wide
        else ()
    )
    return LegalPlacementLattice(
        unit_sites=unit_sites,
        wide_sites=wide_sites,
        forbidden_areas=forbidden_areas,
    )


def _sparse_independent_case() -> _CorpusCase:
    columns = 30
    rows = 10
    entities = [ConstantCombinator(entity_id) for entity_id in range(1, columns * rows + 1)]
    positions = {
        entity.id: (float((entity.id - 1) % columns * 4), float((entity.id - 1) // columns * 4))
        for entity in entities
    }
    circuit = PhysicalCircuit("generic_sparse_independent", entities=entities)
    layout = Layout(circuit, positions, (), (), (), ())
    return _CorpusCase(
        "sparse-independent-300",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(columns * 4, rows * 4),
            safe_wire_span=7.0,
        ),
    )


def _relay_forest_case() -> _CorpusCase:
    pair_count = 100
    endpoint_distance = 140
    row_pitch = 2
    safe_span = 7.0
    entities = [ConstantCombinator(entity_id) for entity_id in range(1, pair_count * 2 + 1)]
    connections: list[WireConnection] = []
    positions: dict[int, tuple[float, float]] = {}
    relays: list[LayoutRelay] = []
    wires: list[LayoutWire] = []
    next_relay_id = len(entities) + 1

    for pair in range(pair_count):
        left_id = pair * 2 + 1
        right_id = left_id + 1
        y = float(pair * row_pitch)
        color = WireColor.RED if pair % 2 == 0 else WireColor.GREEN
        connector_id = 1 if color is WireColor.RED else 2
        positions[left_id] = (0.0, y)
        positions[right_id] = (float(endpoint_distance), y)
        connections.append(
            WireConnection(
                WireEndpoint(left_id, Connector.SINGLE),
                WireEndpoint(right_id, Connector.SINGLE),
                color,
            )
        )

        chain = [left_id]
        for x in range(int(safe_span), endpoint_distance, int(safe_span)):
            relay_id = next_relay_id
            next_relay_id += 1
            position = (float(x), y)
            positions[relay_id] = position
            relays.append(LayoutRelay(relay_id, position, f"hand-routed {color.value} chain"))
            chain.append(relay_id)
        chain.append(right_id)
        wires.extend(
            LayoutWire(left, connector_id, right, connector_id, color)
            for left, right in zip(chain, chain[1:], strict=False)
        )

    circuit = PhysicalCircuit(
        "generic_hand_routed_relay_forest",
        entities=entities,
        connections=connections,
    )
    layout = Layout(circuit, positions, tuple(relays), tuple(wires), (), ())
    return _CorpusCase(
        "hand-routed-forest-200-plus-1900-relays",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(endpoint_distance + 1, pair_count * row_pitch),
            safe_wire_span=safe_span,
        ),
    )


def _shared_bus_case() -> _CorpusCase:
    """One high-degree physical net whose valid routing shares one long trunk."""

    leaf_count = 64
    endpoint_distance = 140
    row_pitch = 2
    safe_span = 7.0
    hub_id = 1
    entities = [ConstantCombinator(entity_id) for entity_id in range(1, leaf_count + 2)]
    positions: dict[int, tuple[float, float]] = {hub_id: (0.0, 0.0)}
    connections: list[WireConnection] = []
    relays: list[LayoutRelay] = []
    wires: list[LayoutWire] = []
    next_relay_id = len(entities) + 1

    leaf_ids = list(range(2, leaf_count + 2))
    for index, leaf_id in enumerate(leaf_ids):
        positions[leaf_id] = (float(endpoint_distance), float(index * row_pitch))
        connections.append(
            WireConnection(
                WireEndpoint(hub_id, Connector.SINGLE),
                WireEndpoint(leaf_id, Connector.SINGLE),
                WireColor.RED,
            )
        )

    trunk = [hub_id]
    for x in range(int(safe_span), endpoint_distance, int(safe_span)):
        relay_id = next_relay_id
        next_relay_id += 1
        position = (float(x), 0.0)
        positions[relay_id] = position
        relays.append(LayoutRelay(relay_id, position, "shared red trunk"))
        trunk.append(relay_id)
    trunk.append(leaf_ids[0])
    wires.extend(
        LayoutWire(left, 1, right, 1, WireColor.RED)
        for left, right in zip(trunk, trunk[1:], strict=False)
    )
    wires.extend(
        LayoutWire(left, 1, right, 1, WireColor.RED)
        for left, right in zip(leaf_ids, leaf_ids[1:], strict=False)
    )

    circuit = PhysicalCircuit(
        "generic_shared_bus",
        entities=entities,
        connections=connections,
    )
    layout = Layout(circuit, positions, tuple(relays), tuple(wires), (), ())
    return _CorpusCase(
        "shared-bus-65-plus-19-relays",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(endpoint_distance + 1, leaf_count * row_pitch),
            safe_wire_span=safe_span,
        ),
    )


def _fixed_endpoint_span_case() -> _CorpusCase:
    """A long valid net with both implementation terminals fixed exactly in place."""

    endpoint_distance = 140
    safe_span = 7.0
    entities = [ConstantCombinator(1), ConstantCombinator(2)]
    positions: dict[int, tuple[float, float]] = {
        1: (0.0, 0.0),
        2: (float(endpoint_distance), 0.0),
    }
    relays: list[LayoutRelay] = []
    chain = [1]
    next_relay_id = 3
    for x in range(int(safe_span), endpoint_distance, int(safe_span)):
        relay_id = next_relay_id
        next_relay_id += 1
        position = (float(x), 0.0)
        positions[relay_id] = position
        relays.append(LayoutRelay(relay_id, position, "fixed-endpoint red chain"))
        chain.append(relay_id)
    chain.append(2)
    wires = tuple(
        LayoutWire(left, 1, right, 1, WireColor.RED)
        for left, right in zip(chain, chain[1:], strict=False)
    )
    circuit = PhysicalCircuit(
        "generic_fixed_endpoint_span",
        entities=entities,
        connections=[
            WireConnection(
                WireEndpoint(1, Connector.SINGLE),
                WireEndpoint(2, Connector.SINGLE),
                WireColor.RED,
            )
        ],
    )
    layout = Layout(circuit, positions, tuple(relays), wires, (), ())
    return _CorpusCase(
        "fixed-endpoint-span-2-plus-19-relays",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(endpoint_distance + 1, 3),
            safe_wire_span=safe_span,
            fixed_positions={1: positions[1], 2: positions[2]},
        ),
    )


def _narrow_corridor_case() -> _CorpusCase:
    """A fixed long span whose only legal short-hop path crosses a one-tile corridor."""

    endpoint_distance = 42
    safe_span = 7.0
    forbidden_areas: tuple[RelayForbiddenArea, ...] = (
        (8.5, 33.5, -4.5, -0.5),
        (8.5, 33.5, 0.5, 4.5),
    )
    entities = [ConstantCombinator(1), ConstantCombinator(2)]
    positions: dict[int, tuple[float, float]] = {1: (0.0, 0.0), 2: (42.0, 0.0)}
    relays: list[LayoutRelay] = []
    chain = [1]
    next_relay_id = 3
    for x in range(7, endpoint_distance, 7):
        relay_id = next_relay_id
        next_relay_id += 1
        position = (float(x), 0.0)
        positions[relay_id] = position
        relays.append(LayoutRelay(relay_id, position, "one-tile corridor red chain"))
        chain.append(relay_id)
    chain.append(2)
    wires = tuple(
        LayoutWire(left, 1, right, 1, WireColor.RED)
        for left, right in zip(chain, chain[1:], strict=False)
    )
    circuit = PhysicalCircuit(
        "generic_narrow_corridor_span",
        entities=entities,
        connections=[
            WireConnection(
                WireEndpoint(1, Connector.SINGLE),
                WireEndpoint(2, Connector.SINGLE),
                WireColor.RED,
            )
        ],
    )
    layout = Layout(circuit, positions, tuple(relays), wires, (), ())
    return _CorpusCase(
        "narrow-corridor-span-2-plus-5-relays",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(
                endpoint_distance + 1,
                9,
                y_offset=-4,
                forbidden_areas=forbidden_areas,
            ),
            safe_wire_span=safe_span,
            fixed_positions={1: positions[1], 2: positions[2]},
        ),
    )


def _mixed_footprint_case() -> _CorpusCase:
    """Independent 1x1 constants and 2x1 arithmetic combinators on one shared lattice."""

    signal = SignalId("virtual", "signal-A")
    constants = [ConstantCombinator(entity_id) for entity_id in range(1, 13)]
    arithmetic = [
        ArithmeticCombinator(
            entity_id,
            "+",
            Operand(constant=1),
            Operand(constant=1),
            output_each=False,
            output_signal=signal,
        )
        for entity_id in range(13, 21)
    ]
    positions: dict[int, tuple[float, float]] = {}
    for index, entity in enumerate(constants):
        positions[entity.id] = (float(1 + (index % 6) * 3), float(8 + index // 6 * 2))
    for index, entity in enumerate(arithmetic):
        positions[entity.id] = (float(2 + (index % 4) * 4), float(2 + index // 4 * 3))
    circuit = PhysicalCircuit("generic_mixed_footprints", entities=[*constants, *arithmetic])
    layout = Layout(circuit, positions, (), (), (), ())
    return _CorpusCase(
        "mixed-footprints-12-unit-plus-8-wide",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(20, 12, include_wide=True),
            safe_wire_span=7.0,
        ),
    )


def _perimeter_anchor_case() -> _CorpusCase:
    """Many fixed public terminals surrounding movable internal implementation entities."""

    lane_count = 6
    endpoint_distance = 42
    safe_span = 7.0
    left_ids = list(range(1, lane_count + 1))
    right_ids = list(range(lane_count + 1, lane_count * 2 + 1))
    body_ids = list(range(lane_count * 2 + 1, lane_count * 3 + 1))
    entities = [
        *[ConstantCombinator(entity_id, annotation_only=True) for entity_id in left_ids],
        *[ConstantCombinator(entity_id, annotation_only=True) for entity_id in right_ids],
        *[ConstantCombinator(entity_id) for entity_id in body_ids],
    ]
    positions: dict[int, tuple[float, float]] = {}
    connections: list[WireConnection] = []
    relays: list[LayoutRelay] = []
    wires: list[LayoutWire] = []
    next_relay_id = lane_count * 3 + 1

    for lane, (left_id, right_id, body_id) in enumerate(
        zip(left_ids, right_ids, body_ids, strict=True)
    ):
        y = float(1 + lane * 2)
        color = WireColor.RED if lane % 2 == 0 else WireColor.GREEN
        connector_id = 1 if color is WireColor.RED else 2
        positions[left_id] = (0.0, y)
        positions[body_id] = (21.0, y)
        positions[right_id] = (42.0, y)
        connections.extend(
            (
                WireConnection(
                    WireEndpoint(left_id, Connector.SINGLE),
                    WireEndpoint(body_id, Connector.SINGLE),
                    color,
                ),
                WireConnection(
                    WireEndpoint(body_id, Connector.SINGLE),
                    WireEndpoint(right_id, Connector.SINGLE),
                    color,
                ),
            )
        )

        chain = [left_id]
        for x in (7, 14):
            relay_id = next_relay_id
            next_relay_id += 1
            position = (float(x), y)
            positions[relay_id] = position
            relays.append(LayoutRelay(relay_id, position, "anchored interface relay"))
            chain.append(relay_id)
        chain.append(body_id)
        for x in (28, 35):
            relay_id = next_relay_id
            next_relay_id += 1
            position = (float(x), y)
            positions[relay_id] = position
            relays.append(LayoutRelay(relay_id, position, "anchored interface relay"))
            chain.append(relay_id)
        chain.append(right_id)
        wires.extend(
            LayoutWire(left, connector_id, right, connector_id, color)
            for left, right in zip(chain, chain[1:], strict=False)
        )

    circuit = PhysicalCircuit(
        "generic_perimeter_anchors",
        entities=entities,
        connections=connections,
        inputs=[InputPort(f"input-{lane}", left_id, None) for lane, left_id in enumerate(left_ids)],
        outputs=[
            OutputPort(f"output-{lane}", right_id, None, 0)
            for lane, right_id in enumerate(right_ids)
        ],
    )
    layout = Layout(circuit, positions, tuple(relays), tuple(wires), (), ())
    fixed_positions = {entity_id: positions[entity_id] for entity_id in (*left_ids, *right_ids)}
    return _CorpusCase(
        "perimeter-anchors-12-plus-6-body-plus-24-relays",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(endpoint_distance + 1, lane_count * 2 + 1),
            safe_wire_span=safe_span,
            fixed_positions=fixed_positions,
        ),
    )


def _run_case(
    case: _CorpusCase,
    *,
    proposals: int,
    seed: int,
) -> LayoutOptimizationResult:
    validate_physical_layout(case.problem)
    started = monotonic()
    result = optimize_physical_layout(
        case.problem,
        options=PlacementOptions(
            anchor_io=False,
            reserve_corridors=False,
            iterations=proposals,
            random_seed=seed,
            restarts=1,
        ),
    )
    validate_physical_layout(replace(case.problem, layout=result.layout))
    elapsed = monotonic() - started
    if result.after.objective > result.before.objective:
        raise AssertionError(f"{case.name} returned a worse physical objective")
    print(
        f"{case.name} seed={seed}: "
        f"input=({result.before.implementation_entities} implementation, "
        f"{result.before.relay_count} relays, area {result.before.occupied_area:.1f}, "
        f"wire {result.before.wire_length:.1f}); "
        f"output=({result.after.implementation_entities} implementation, "
        f"{result.after.relay_count} relays, area {result.after.occupied_area:.1f}, "
        f"wire {result.after.wire_length:.1f}); "
        f"work={result.proposal_budget} proposals; runtime={elapsed:.2f}s"
    )
    for diagnostic in result.diagnostics:
        print(f"  diagnostic: {diagnostic}")
    return result


def _run_seed_sweep(
    case: _CorpusCase,
    *,
    proposals: int,
    first_seed: int,
    seeds: int,
) -> list[LayoutOptimizationResult]:
    results = [
        _run_case(case, proposals=proposals, seed=first_seed + offset) for offset in range(seeds)
    ]
    if seeds > 1:
        objectives = [result.after.objective for result in results]
        relay_counts = [result.after.relay_count for result in results]
        areas = [result.after.occupied_area for result in results]
        wire_lengths = [result.after.wire_length for result in results]
        print(
            f"{case.name} summary over {seeds} seeds: "
            f"best={min(objectives)}; "
            f"median relays={median(relay_counts):.1f}, "
            f"area={median(areas):.1f}, wire={median(wire_lengths):.1f}; "
            f"worst={max(objectives)}"
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0, help="first random seed")
    parser.add_argument("--seeds", type=int, default=1, help="number of consecutive seeds to run")
    args = parser.parse_args()
    if args.proposals < 0:
        parser.error("--proposals must be non-negative")
    if args.seeds <= 0:
        parser.error("--seeds must be positive")

    cases = (
        _sparse_independent_case(),
        _relay_forest_case(),
        _shared_bus_case(),
        _fixed_endpoint_span_case(),
        _narrow_corridor_case(),
        _mixed_footprint_case(),
        _perimeter_anchor_case(),
    )
    results = {
        case.name: _run_seed_sweep(
            case,
            proposals=args.proposals,
            first_seed=args.seed,
            seeds=args.seeds,
        )
        for case in cases
    }

    sparse = results["sparse-independent-300"]
    forest = results["hand-routed-forest-200-plus-1900-relays"]
    if min(result.after.occupied_area for result in sparse) >= sparse[0].before.occupied_area / 2:
        raise AssertionError("sparse generic case did not compact substantially in any seed")
    if min(result.after.relay_count for result in forest) >= forest[0].before.relay_count / 2:
        raise AssertionError(
            "hand-routed forest did not remove substantial relay topology in any seed"
        )


if __name__ == "__main__":
    main()
