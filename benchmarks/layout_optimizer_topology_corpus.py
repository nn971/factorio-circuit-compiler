"""Topology/scale tranche for the generic routed-layout optimizer corpus.

This module complements :mod:`benchmarks.layout_optimizer_corpus` with cases that are easier to
reason about separately: clustered local nets with sparse cuts, red/green mesh connectivity, an
already-packed near-optimal seed, and an explicitly opt-in 1k+ object scale case.
"""

from __future__ import annotations

import argparse

from benchmarks.layout_optimizer_corpus import (
    _CorpusCase,
    _rectangular_lattice,
    _run_seed_sweep,
)
from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    PhysicalCircuit,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import LayoutOptimizationProblem


def _clustered_sparse_cut_case() -> _CorpusCase:
    """Three compact red clusters joined only by one sparse green backbone."""

    safe_span = 7.0
    cluster_bases = (0, 28, 56)
    cluster_size = 3
    entities: list[ConstantCombinator] = []
    positions: dict[int, tuple[float, float]] = {}
    connections: list[WireConnection] = []
    wires: list[LayoutWire] = []
    relays: list[LayoutRelay] = []
    cluster_hubs: list[int] = []
    next_entity_id = 1

    for base_x in cluster_bases:
        cluster_ids: list[int] = []
        for row in range(cluster_size):
            for column in range(cluster_size):
                entity_id = next_entity_id
                next_entity_id += 1
                entities.append(ConstantCombinator(entity_id))
                positions[entity_id] = (float(base_x + column * 2), float(row * 2))
                cluster_ids.append(entity_id)

        hub_id = cluster_ids[4]
        cluster_hubs.append(hub_id)
        for entity_id in cluster_ids:
            if entity_id == hub_id:
                continue
            connections.append(
                WireConnection(
                    WireEndpoint(hub_id, Connector.SINGLE),
                    WireEndpoint(entity_id, Connector.SINGLE),
                    WireColor.RED,
                )
            )
            wires.append(LayoutWire(hub_id, 1, entity_id, 1, WireColor.RED))

    next_relay_id = next_entity_id
    for left_hub, right_hub in zip(cluster_hubs, cluster_hubs[1:], strict=False):
        connections.append(
            WireConnection(
                WireEndpoint(left_hub, Connector.SINGLE),
                WireEndpoint(right_hub, Connector.SINGLE),
                WireColor.GREEN,
            )
        )
        left_x, y = positions[left_hub]
        right_x, _ = positions[right_hub]
        chain = [left_hub]
        x = int(left_x) + 7
        while x < int(right_x):
            relay_id = next_relay_id
            next_relay_id += 1
            position = (float(x), y)
            positions[relay_id] = position
            relays.append(LayoutRelay(relay_id, position, "sparse green inter-cluster cut"))
            chain.append(relay_id)
            x += 7
        chain.append(right_hub)
        wires.extend(
            LayoutWire(left, 2, right, 2, WireColor.GREEN)
            for left, right in zip(chain, chain[1:], strict=False)
        )

    circuit = PhysicalCircuit(
        "generic_clustered_sparse_cut",
        entities=entities,
        connections=connections,
    )
    layout = Layout(circuit, positions, tuple(relays), tuple(wires), (), ())
    return _CorpusCase(
        "clustered-27-plus-6-cut-relays",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(61, 5),
            safe_wire_span=safe_span,
        ),
    )


def _red_green_mesh_case() -> _CorpusCase:
    """A 5x5 crossing mesh: rows are red nets and columns are green nets."""

    side = 5
    entities = [ConstantCombinator(entity_id) for entity_id in range(1, side * side + 1)]
    positions: dict[int, tuple[float, float]] = {}
    connections: list[WireConnection] = []
    wires: list[LayoutWire] = []

    def entity_id(row: int, column: int) -> int:
        return row * side + column + 1

    for row in range(side):
        for column in range(side):
            positions[entity_id(row, column)] = (float(1 + column * 2), float(1 + row * 2))

    for row in range(side):
        for column in range(side - 1):
            left = entity_id(row, column)
            right = entity_id(row, column + 1)
            connections.append(
                WireConnection(
                    WireEndpoint(left, Connector.SINGLE),
                    WireEndpoint(right, Connector.SINGLE),
                    WireColor.RED,
                )
            )
            wires.append(LayoutWire(left, 1, right, 1, WireColor.RED))

    for column in range(side):
        for row in range(side - 1):
            top = entity_id(row, column)
            bottom = entity_id(row + 1, column)
            connections.append(
                WireConnection(
                    WireEndpoint(top, Connector.SINGLE),
                    WireEndpoint(bottom, Connector.SINGLE),
                    WireColor.GREEN,
                )
            )
            wires.append(LayoutWire(top, 2, bottom, 2, WireColor.GREEN))

    circuit = PhysicalCircuit(
        "generic_red_green_mesh",
        entities=entities,
        connections=connections,
    )
    layout = Layout(circuit, positions, (), tuple(wires), (), ())
    return _CorpusCase(
        "red-green-mesh-25",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(11, 11),
            safe_wire_span=7.0,
        ),
    )


def _near_optimal_packed_case() -> _CorpusCase:
    """A fully packed 8x6 independent layout with no empty legal sites inside its envelope."""

    width = 8
    height = 6
    entities = [ConstantCombinator(entity_id) for entity_id in range(1, width * height + 1)]
    positions = {
        entity.id: (float((entity.id - 1) % width), float((entity.id - 1) // width))
        for entity in entities
    }
    circuit = PhysicalCircuit("generic_near_optimal_packed", entities=entities)
    layout = Layout(circuit, positions, (), (), (), ())
    return _CorpusCase(
        "near-optimal-packed-48",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(width, height),
            safe_wire_span=7.0,
        ),
    )


def _large_sparse_case() -> _CorpusCase:
    """A 1,200-object sparse compaction case kept out of routine pytest."""

    columns = 40
    rows = 30
    entities = [ConstantCombinator(entity_id) for entity_id in range(1, columns * rows + 1)]
    positions = {
        entity.id: (
            float(((entity.id - 1) % columns) * 2),
            float(((entity.id - 1) // columns) * 2),
        )
        for entity in entities
    }
    circuit = PhysicalCircuit("generic_large_sparse_1200", entities=entities)
    layout = Layout(circuit, positions, (), (), (), ())
    return _CorpusCase(
        "large-sparse-independent-1200",
        LayoutOptimizationProblem(
            layout,
            _rectangular_lattice(columns * 2, rows * 2),
            safe_wire_span=7.0,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0, help="first random seed")
    parser.add_argument("--seeds", type=int, default=1, help="number of consecutive seeds to run")
    parser.add_argument(
        "--include-scale",
        action="store_true",
        help="also run the 1,200-object sparse scale case",
    )
    args = parser.parse_args()
    if args.proposals < 0:
        parser.error("--proposals must be non-negative")
    if args.seeds <= 0:
        parser.error("--seeds must be positive")

    cases = [
        _clustered_sparse_cut_case(),
        _red_green_mesh_case(),
        _near_optimal_packed_case(),
    ]
    if args.include_scale:
        cases.append(_large_sparse_case())

    for case in cases:
        _run_seed_sweep(
            case,
            proposals=args.proposals,
            first_seed=args.seed,
            seeds=args.seeds,
        )


if __name__ == "__main__":
    main()
