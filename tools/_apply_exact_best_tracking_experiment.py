from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one match in {path}: {old[:100]!r}")
    file.write_text(text.replace(old, new, 1))


incremental = "src/factorio_circuit/synthesis/incremental_joint_layout.py"
helper = '''def _accepted_move_exact_score(
    state: exact._JointState,
    topology: _FeasibleTopology,
    center: Position,
) -> tuple[int, float, float]:
    """Measure the public lexicographic objective after one accepted hot-loop move."""

    return _exact_score(state, topology, center)


'''
replace_once(incremental, "def _anneal_feasible(\n", helper + "def _anneal_feasible(\n")
replace_once(
    incremental,
    '''            topology.total_energy += wire_delta\n\n        topology = _simplify_feasible_topology(state, topology)\n''',
    '''            topology.total_energy += wire_delta\n\n            accepted_score = _accepted_move_exact_score(state, topology, center)\n            if accepted_score < best_score:\n                best_score = accepted_score\n                best_positions = dict(state.positions)\n                best_relays = dict(state.relay_positions)\n                best_relay_groups = dict(state.relay_groups)\n                best_routing = topology.routing\n\n        topology = _simplify_feasible_topology(state, topology)\n''',
)

observability = "src/factorio_circuit/synthesis/layout_observability.py"
replace_once(
    observability,
    '''        for step in range(epoch_start, epoch_end):\n            stats.proposals_attempted += 1\n''',
    '''        epoch_improved = False\n        for step in range(epoch_start, epoch_end):\n            stats.proposals_attempted += 1\n''',
)
replace_once(
    observability,
    '''            if other is not None:\n                stats.swaps_accepted += 1\n\n        topology = incremental._simplify_feasible_topology(state, topology)\n''',
    '''            if other is not None:\n                stats.swaps_accepted += 1\n\n            accepted_score = incremental._accepted_move_exact_score(state, topology, center)\n            if accepted_score < best_score:\n                best_score = accepted_score\n                best_positions = dict(state.positions)\n                best_relays = dict(state.relay_positions)\n                best_relay_groups = dict(state.relay_groups)\n                best_routing = topology.routing\n                epoch_improved = True\n\n        topology = incremental._simplify_feasible_topology(state, topology)\n''',
)
replace_once(
    observability,
    '''        score = incremental._exact_score(state, topology, center)\n        improved = score < best_score\n        if improved:\n            best_score = score\n            best_positions = dict(state.positions)\n            best_relays = dict(state.relay_positions)\n            best_relay_groups = dict(state.relay_groups)\n            best_routing = topology.routing\n        _complete_epoch(stats, best_score=best_score, improved=improved)\n''',
    '''        score = incremental._exact_score(state, topology, center)\n        if score < best_score:\n            best_score = score\n            best_positions = dict(state.positions)\n            best_relays = dict(state.relay_positions)\n            best_relay_groups = dict(state.relay_groups)\n            best_routing = topology.routing\n            epoch_improved = True\n        _complete_epoch(stats, best_score=best_score, improved=epoch_improved)\n''',
)

regressions = "tests/synthesis/test_incremental_joint_regressions.py"
append = '''\n\ndef test_annealer_retains_exact_best_state_seen_inside_an_epoch(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    physical = PhysicalCircuit(\n        "mid_epoch_best",\n        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2)],\n    )\n    state = exact._JointState(\n        circuit=physical,\n        endpoints_by_group={},\n        colors_by_group={},\n        positions={1: (0.0, 0.0), 2: (10.0, 0.0)},\n        relay_positions={},\n        relay_groups={},\n        safe_span=100.0,\n        forbidden_areas=(),\n    )\n    topology = incremental._FeasibleTopology.build(\n        state,\n        RoutingPlan(relays=(), wires=()),\n    )\n    options = PlacementOptions(\n        anchor_io=False,\n        iterations=2,\n        reserve_corridors=False,\n        target_fill=0.6,\n    )\n    grid = incremental.base_placement._candidate_grid(8, 1, options)\n    proposed = iter(((1.0, 0.0), (2.0, 0.0)))\n    exact_scores = iter(\n        (\n            (0, 100.0, 100.0),\n            (0, 50.0, 50.0),\n            (0, 80.0, 80.0),\n        )\n    )\n    first_accepted_positions: dict[int, tuple[float, float]] = {}\n\n    monkeypatch.setattr(\n        incremental,\n        "_proposed_position",\n        lambda *_args, **_kwargs: next(proposed),\n    )\n    monkeypatch.setattr(incremental, "_position_is_legal", lambda *_args, **_kwargs: True)\n    monkeypatch.setattr(incremental, "_rectangle_overflow", lambda *_args, **_kwargs: 0.0)\n    monkeypatch.setattr(exact, "_compactness", lambda *_args, **_kwargs: 0.0)\n    monkeypatch.setattr(\n        incremental,\n        "_exact_score",\n        lambda *_args, **_kwargs: (0, 80.0, 80.0),\n    )\n\n    def accepted_score(\n        observed_state: exact._JointState,\n        _topology: incremental._FeasibleTopology,\n        _center: tuple[float, float],\n    ) -> tuple[int, float, float]:\n        score = next(exact_scores)\n        if score == (0, 50.0, 50.0):\n            first_accepted_positions.update(observed_state.positions)\n        return score\n\n    # The initial exact best is supplied separately so the helper sequence describes accepted moves.\n    initial_calls = 0\n\n    def exact_score(\n        _state: exact._JointState,\n        _topology: incremental._FeasibleTopology,\n        _center: tuple[float, float],\n    ) -> tuple[int, float, float]:\n        nonlocal initial_calls\n        initial_calls += 1\n        return (0, 100.0, 100.0) if initial_calls == 1 else (0, 80.0, 80.0)\n\n    monkeypatch.setattr(incremental, "_exact_score", exact_score)\n    monkeypatch.setattr(incremental, "_accepted_move_exact_score", accepted_score)\n\n    incremental._anneal_feasible(state, topology, options, grid)\n\n    assert first_accepted_positions\n    assert state.positions == first_accepted_positions\n'''
Path(regressions).write_text(Path(regressions).read_text() + append)

roadmap = "docs/roadmap.md"
replace_once(
    roadmap,
    '''5. only after measurement, lower-level performance work in occupancy, routing indexes, and proposal evaluation.\n\n### C acceptance\n''',
    '''5. only after measurement, lower-level performance work in occupancy, routing indexes, and proposal evaluation.\n\n### Experiment record\n\n- **Rejected: adaptive coarse retopology.** In 12 paired runs it produced 0 better / 12 equal / 0 worse physical objectives while adding four rebuilds per run and increasing routing work/runtime.\n- **Rejected: terminal + one adjacent relay translation.** In 18 paired runs it produced 0 better / 18 equal / 0 worse objectives. It attempted 13,096 rescues and accepted none because taut safe-span chains transferred the violation to the relay's far side.\n- **Rejected: seven-step reach backoff.** In 18 paired runs it produced 0 better / 17 equal / 1 worse objectives. It reduced some reach rejections but made taut/fixed cases 2.6x-4.5x slower.\n- **Rejected: analytical implementation reach clipping.** In 18 paired runs it produced 0 better / 15 equal / 3 worse objectives. It cheaply removed many reach rejections, but every clustered sparse-cut seed became lexicographically worse by trading larger area for shorter wire.\n- **Current: exact mid-epoch best tracking.** The annealer currently samples the true `(relay_count, occupied_area, wire_length)` objective only at epoch boundaries. Record any better exact state immediately after an accepted move without changing the proposal, RNG, or acceptance trajectory.\n\n### C acceptance\n''',
)
replace_once(
    roadmap,
    '''Begin **Milestone C** with adaptive coarse retopology at the existing safe epoch boundary. Preserve the fixed-fraction rebuild schedule as a baseline fallback, add conservative triggers for sustained objective stagnation or wire-reach pressure, and keep a cooldown between adaptive rebuilds. Use the Milestone B counters and the Milestone A corpus to compare quality and routing work across seeds before accepting broader proposal or compound-move changes.''',
    '''Continue **Milestone C** with exact mid-epoch best tracking. This experiment deliberately leaves the visited annealing trajectory unchanged and only samples the public lexicographic objective after accepted moves, so a fixed seed cannot lose a state the baseline would have returned. Keep it only if the corpus shows useful objective gains at acceptable scoring overhead; if the idea is valuable but expensive, optimize the exact-score update incrementally rather than weakening the objective check.''',
)
