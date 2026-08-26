"""Temporarily stage the measured relay-relief Annealing-v2 experiment."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "src/factorio_circuit/synthesis/incremental_joint_layout.py"
OBSERVED = ROOT / "src/factorio_circuit/synthesis/layout_observability.py"
TEST = ROOT / "tests/synthesis/test_layout_relay_relief.py"


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one staging anchor in {path}, found {count}")
    path.write_text(text.replace(old, new))


def _stage_production() -> None:
    _replace_once(
        PRODUCTION,
        """    _ = topology, center\n    return tracker.score(state)\n\n\ndef _anneal_feasible(\n""",
        """    _ = topology, center\n    return tracker.score(state)\n\n\ndef _should_schedule_relay_relief(\n    proposal_pool: list[int],\n    *,\n    implementation_ids: set[int],\n    movable_relays: list[int],\n    accepted_moves: int,\n    reach_rejections: int,\n    proposal_count: int,\n) -> bool:\n    \"\"\"Schedule one relay-only epoch after a measured implementation reach deadlock.\"\"\"\n\n    return (\n        proposal_count > 0\n        and accepted_moves == 0\n        and bool(movable_relays)\n        and bool(proposal_pool)\n        and all(item in implementation_ids for item in proposal_pool)\n        and 2 * reach_rejections >= proposal_count\n    )\n\n\ndef _anneal_feasible(\n""",
    )
    _replace_once(
        PRODUCTION,
        """    topology_rebuilds = {\n        min(\n            iterations,\n            ceil(iterations * fraction / _EPOCH_PROPOSALS) * _EPOCH_PROPOSALS,\n        )\n        for fraction in _ANNEAL_REBUILD_FRACTIONS\n    }\n\n    for epoch_start in range(0, iterations, _EPOCH_PROPOSALS):\n""",
        """    topology_rebuilds = {\n        min(\n            iterations,\n            ceil(iterations * fraction / _EPOCH_PROPOSALS) * _EPOCH_PROPOSALS,\n        )\n        for fraction in _ANNEAL_REBUILD_FRACTIONS\n    }\n    relay_relief_pending = False\n    relay_relief_exhausted = False\n\n    for epoch_start in range(0, iterations, _EPOCH_PROPOSALS):\n""",
    )
    _replace_once(
        PRODUCTION,
        """        implementation_outliers = [item for item in outliers if item in state.positions]\n        proposal_pool = (\n            implementation_outliers\n            or outliers\n            or (movable_entities if movable_entities else movable_relays)\n        )\n        if not proposal_pool:\n            continue\n\n        for step in range(epoch_start, epoch_end):\n""",
        """        implementation_outliers = [item for item in outliers if item in state.positions]\n        base_proposal_pool = (\n            implementation_outliers\n            or outliers\n            or (movable_entities if movable_entities else movable_relays)\n        )\n        relay_relief_active = relay_relief_pending and bool(movable_relays)\n        proposal_pool = movable_relays if relay_relief_active else base_proposal_pool\n        relay_relief_pending = False\n        if not proposal_pool:\n            continue\n\n        epoch_accepted_moves = 0\n        epoch_reach_rejections = 0\n        epoch_proposal_count = epoch_end - epoch_start\n        for step in range(epoch_start, epoch_end):\n""",
    )
    _replace_once(
        PRODUCTION,
        """            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                continue\n""",
        """            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                epoch_reach_rejections += 1\n                continue\n""",
    )
    _replace_once(
        PRODUCTION,
        """            topology.total_energy += wire_delta\n\n            if exact_tracker is not None:\n""",
        """            topology.total_energy += wire_delta\n            epoch_accepted_moves += 1\n\n            if exact_tracker is not None:\n""",
    )
    _replace_once(
        PRODUCTION,
        """                    best_routing = topology.routing\n\n        topology = _simplify_feasible_topology(state, topology)\n""",
        """                    best_routing = topology.routing\n\n        if relay_relief_active:\n            if epoch_accepted_moves == 0:\n                relay_relief_exhausted = True\n        elif (\n            not relay_relief_exhausted\n            and _should_schedule_relay_relief(\n                base_proposal_pool,\n                implementation_ids=set(state.positions),\n                movable_relays=movable_relays,\n                accepted_moves=epoch_accepted_moves,\n                reach_rejections=epoch_reach_rejections,\n                proposal_count=epoch_proposal_count,\n            )\n        ):\n            relay_relief_pending = True\n\n        topology = _simplify_feasible_topology(state, topology)\n""",
    )


def _stage_observed() -> None:
    _replace_once(
        OBSERVED,
        """    topology_rebuilds = {\n        min(\n            iterations,\n            ceil(iterations * fraction / incremental._EPOCH_PROPOSALS)\n            * incremental._EPOCH_PROPOSALS,\n        )\n        for fraction in incremental._ANNEAL_REBUILD_FRACTIONS\n    }\n\n    for epoch_start in range(0, iterations, incremental._EPOCH_PROPOSALS):\n""",
        """    topology_rebuilds = {\n        min(\n            iterations,\n            ceil(iterations * fraction / incremental._EPOCH_PROPOSALS)\n            * incremental._EPOCH_PROPOSALS,\n        )\n        for fraction in incremental._ANNEAL_REBUILD_FRACTIONS\n    }\n    relay_relief_pending = False\n    relay_relief_exhausted = False\n\n    for epoch_start in range(0, iterations, incremental._EPOCH_PROPOSALS):\n""",
    )
    _replace_once(
        OBSERVED,
        """        implementation_outliers = [item for item in outliers if item in state.positions]\n        proposal_pool = (\n            implementation_outliers\n            or outliers\n            or (movable_entities if movable_entities else movable_relays)\n        )\n        if not proposal_pool:\n            _complete_epoch(stats, best_score=best_score, improved=False)\n            continue\n\n        epoch_improved = False\n        for step in range(epoch_start, epoch_end):\n""",
        """        implementation_outliers = [item for item in outliers if item in state.positions]\n        base_proposal_pool = (\n            implementation_outliers\n            or outliers\n            or (movable_entities if movable_entities else movable_relays)\n        )\n        relay_relief_active = relay_relief_pending and bool(movable_relays)\n        proposal_pool = movable_relays if relay_relief_active else base_proposal_pool\n        relay_relief_pending = False\n        if not proposal_pool:\n            _complete_epoch(stats, best_score=best_score, improved=False)\n            continue\n\n        epoch_improved = False\n        epoch_accepted_moves = 0\n        epoch_reach_rejections = 0\n        epoch_proposal_count = epoch_end - epoch_start\n        for step in range(epoch_start, epoch_end):\n""",
    )
    _replace_once(
        OBSERVED,
        """            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                stats.wire_reach_rejections += 1\n                continue\n""",
        """            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                stats.wire_reach_rejections += 1\n                epoch_reach_rejections += 1\n                continue\n""",
    )
    _replace_once(
        OBSERVED,
        """            topology.total_energy += wire_delta\n            stats.accepted_moves += 1\n""",
        """            topology.total_energy += wire_delta\n            stats.accepted_moves += 1\n            epoch_accepted_moves += 1\n""",
    )
    _replace_once(
        OBSERVED,
        """                    best_routing = topology.routing\n                    epoch_improved = True\n\n        topology = incremental._simplify_feasible_topology(state, topology)\n""",
        """                    best_routing = topology.routing\n                    epoch_improved = True\n\n        if relay_relief_active:\n            if epoch_accepted_moves == 0:\n                relay_relief_exhausted = True\n        elif (\n            not relay_relief_exhausted\n            and incremental._should_schedule_relay_relief(\n                base_proposal_pool,\n                implementation_ids=set(state.positions),\n                movable_relays=movable_relays,\n                accepted_moves=epoch_accepted_moves,\n                reach_rejections=epoch_reach_rejections,\n                proposal_count=epoch_proposal_count,\n            )\n        ):\n            relay_relief_pending = True\n\n        topology = incremental._simplify_feasible_topology(state, topology)\n""",
    )


def _write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations\n\nfrom benchmarks.layout_optimizer_corpus import _perimeter_anchor_case\nfrom factorio_circuit.synthesis import incremental_joint_layout as incremental\nfrom factorio_circuit.synthesis.layout_observability import optimize_physical_layout_observed\nfrom factorio_circuit.synthesis.placement import PlacementOptions\n\n\ndef test_relay_relief_trigger_requires_implementation_reach_deadlock() -> None:\n    kwargs = dict(\n        proposal_pool=[1, 2],\n        implementation_ids={1, 2},\n        movable_relays=[10, 11],\n        accepted_moves=0,\n        reach_rejections=128,\n        proposal_count=256,\n    )\n    assert incremental._should_schedule_relay_relief(**kwargs)\n    assert not incremental._should_schedule_relay_relief(**{**kwargs, "accepted_moves": 1})\n    assert not incremental._should_schedule_relay_relief(**{**kwargs, "reach_rejections": 127})\n    assert not incremental._should_schedule_relay_relief(**{**kwargs, "movable_relays": []})\n    assert not incremental._should_schedule_relay_relief(\n        **{**kwargs, "proposal_pool": [1, 10]}\n    )\n\n\ndef test_perimeter_anchor_deadlock_schedules_relay_relief() -> None:\n    case = _perimeter_anchor_case()\n    observed = optimize_physical_layout_observed(\n        case.problem,\n        options=PlacementOptions(\n            anchor_io=False,\n            reserve_corridors=False,\n            iterations=512,\n            random_seed=0,\n            restarts=1,\n        ),\n    )\n\n    assert observed.stats.implementation_proposals > 0\n    assert observed.stats.relay_proposals > 0\n'''\n    )


def main() -> None:
    _stage_production()
    _stage_observed()
    _write_test()


if __name__ == "__main__":
    main()
