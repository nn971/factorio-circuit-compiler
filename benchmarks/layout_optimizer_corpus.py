"""Opt-in scalability/quality corpus for the generic physical-layout optimizer.

The cases are deliberately synthetic and unrelated to compiler construction strategies. They feed
complete hand-built routed :class:`Layout` objects through the same public API used by the
safe-folded Snake benchmark.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from time import monotonic

from factorio_circuit.ir.physical import (
    Connector,
    ConstantCombinator,
    PhysicalCircuit,
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
from factorio_circuit.synthesis.placement import PlacementOptions


@dataclass(frozen=True, slots=True)
class _CorpusCase:
    name: str
    problem: LayoutOptimizationProblem


def _constant_lattice(width: int, height: int) -> LegalPlacementLattice:
    return LegalPlacementLattice(
        unit_sites=tuple((float(x), float(y)) for y in range(height) for x in range(width)),
        wide_sites=(),
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
            _constant_lattice(columns * 4, rows * 4),
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
            _constant_lattice(endpoint_distance + 1, pair_count * row_pitch),
            safe_wire_span=safe_span,
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
        f"{case.name}: "
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    sparse = _run_case(_sparse_independent_case(), proposals=args.proposals, seed=args.seed)
    forest = _run_case(_relay_forest_case(), proposals=args.proposals, seed=args.seed)
    if sparse.after.occupied_area >= sparse.before.occupied_area / 2:
        raise AssertionError("sparse generic case did not compact substantially")
    if forest.after.relay_count >= forest.before.relay_count / 2:
        raise AssertionError("hand-routed forest did not remove substantial relay topology")


if __name__ == "__main__":
    main()
