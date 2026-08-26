from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one match in {path}: {old[:80]!r}")
    file.write_text(text.replace(old, new, 1))


incremental = "src/factorio_circuit/synthesis/incremental_joint_layout.py"
replace_once(
    incremental,
    "_ANNEAL_REBUILD_FRACTIONS = (0.25, 0.50, 0.75)\n_BOOTSTRAP_EXPANSIONS = 4\n",
    "_ANNEAL_REBUILD_FRACTIONS = (0.25, 0.50, 0.75)\n"
    "_REACH_BACKOFF_FRACTIONS = (0.875, 0.75, 0.625, 0.5, 0.375, 0.25, 0.125)\n"
    "_BOOTSTRAP_EXPANSIONS = 4\n",
)
helper = '''def _snap_backoff_candidate(
    state: exact._JointState,
    object_id: int,
    target: Position,
    grid: base_placement._GridGeometry,
) -> Position:
    if object_id in state.relay_positions:
        x = min(grid.unit_x_positions, key=lambda value: (abs(value - target[0]), value))
        y = min(grid.y_positions, key=lambda value: (abs(value - target[1]), value))
        return (x, y)
    entity = state.circuit.entity_by_id(object_id)
    return base_placement._nearest_candidate(entity, target, grid)


def _reach_safe_backoff_target(
    state: exact._JointState,
    topology: _FeasibleTopology,
    object_id: int,
    desired: Position,
    occupancy: _SpatialOccupancy,
    grid: base_placement._GridGeometry,
    unit_sites: set[Position],
    wide_sites: set[Position],
) -> Position | None:
    """Back off an over-reaching single-object proposal along the same direction.

    The original proposal remains authoritative. This helper is consulted only after that target
    fails wire reach and only for a non-swap move. It adds no random choices: progressively shorter
    fractions of the same displacement are snapped to the object's legal grid and the first empty,
    reach-safe candidate is returned.
    """

    current = state.object_position(object_id)
    seen = {current, desired}
    for fraction in _REACH_BACKOFF_FRACTIONS:
        interpolated = (
            current[0] + (desired[0] - current[0]) * fraction,
            current[1] + (desired[1] - current[1]) * fraction,
        )
        candidate = _snap_backoff_candidate(state, object_id, interpolated, grid)
        if candidate in seen:
            continue
        seen.add(candidate)
        if not _position_is_legal(state, object_id, candidate, unit_sites, wide_sites):
            continue
        if occupancy.overlaps(object_id, candidate, ignored={object_id}):
            continue
        if topology.proposal_delta(state, {object_id: candidate}) is not None:
            return candidate
    return None


'''
replace_once(incremental, "def _anneal_feasible(\n", helper + "def _anneal_feasible(\n")
replace_once(
    incremental,
    '''            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                continue\n''',
    '''            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None and other is None:\n                backoff_target = _reach_safe_backoff_target(\n                    state,\n                    topology,\n                    object_id,\n                    target,\n                    occupancy,\n                    grid,\n                    unit_sites,\n                    wide_sites,\n                )\n                if backoff_target is not None:\n                    target = backoff_target\n                    targets = {object_id: target}\n                    wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                continue\n''',
)

observability = "src/factorio_circuit/synthesis/layout_observability.py"
for marker in (
    "    swaps_accepted: int = 0\n    relay_deletions: int = 0\n",
):
    replacement = (
        "    swaps_accepted: int = 0\n"
        "    reach_backoff_attempts: int = 0\n"
        "    reach_backoff_feasible_targets: int = 0\n"
        "    reach_backoff_moves_accepted: int = 0\n"
        "    relay_deletions: int = 0\n"
    )
    text = Path(observability).read_text()
    if text.count(marker) != 2:
        raise RuntimeError("expected OptimizationStats and mutable stats field markers")
    Path(observability).write_text(text.replace(marker, replacement))
replace_once(
    observability,
    "            swaps_accepted=self.swaps_accepted,\n            relay_deletions=self.relay_deletions,\n",
    "            swaps_accepted=self.swaps_accepted,\n"
    "            reach_backoff_attempts=self.reach_backoff_attempts,\n"
    "            reach_backoff_feasible_targets=self.reach_backoff_feasible_targets,\n"
    "            reach_backoff_moves_accepted=self.reach_backoff_moves_accepted,\n"
    "            relay_deletions=self.relay_deletions,\n",
)
replace_once(
    observability,
    '''            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                stats.wire_reach_rejections += 1\n                continue\n''',
    '''            wire_delta = topology.proposal_delta(state, targets)\n            backoff_used = False\n            if wire_delta is None and other is None:\n                stats.reach_backoff_attempts += 1\n                backoff_target = incremental._reach_safe_backoff_target(\n                    state,\n                    topology,\n                    object_id,\n                    target,\n                    occupancy,\n                    grid,\n                    unit_sites,\n                    wide_sites,\n                )\n                if backoff_target is not None:\n                    stats.reach_backoff_feasible_targets += 1\n                    backoff_used = True\n                    target = backoff_target\n                    targets = {object_id: target}\n                    wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                stats.wire_reach_rejections += 1\n                continue\n''',
)
replace_once(
    observability,
    '''            if other is not None:\n                stats.swaps_accepted += 1\n\n        topology = incremental._simplify_feasible_topology(state, topology)\n''',
    '''            if other is not None:\n                stats.swaps_accepted += 1\n            if backoff_used:\n                stats.reach_backoff_moves_accepted += 1\n\n        topology = incremental._simplify_feasible_topology(state, topology)\n''',
)

benchmark = "benchmarks/layout_optimizer_observability.py"
replace_once(
    benchmark,
    '''        f"proposal-kind(implementation={stats.implementation_proposals}, "\n        f"relay={stats.relay_proposals}), swaps={stats.swaps_accepted}/{stats.swap_attempts}, "\n''',
    '''        f"proposal-kind(implementation={stats.implementation_proposals}, "\n        f"relay={stats.relay_proposals}), swaps={stats.swaps_accepted}/{stats.swap_attempts}, "\n        f"backoff={stats.reach_backoff_moves_accepted}/"\n        f"{stats.reach_backoff_feasible_targets}/{stats.reach_backoff_attempts}, "\n''',
)

test_observability = "tests/synthesis/test_layout_observability.py"
replace_once(
    test_observability,
    "    assert stats.swaps_accepted <= stats.swap_attempts\n",
    "    assert stats.swaps_accepted <= stats.swap_attempts\n"
    "    assert stats.reach_backoff_moves_accepted <= stats.reach_backoff_feasible_targets\n"
    "    assert stats.reach_backoff_feasible_targets <= stats.reach_backoff_attempts\n",
)

Path("tests/synthesis/test_reach_backoff.py").write_text(
    '''from __future__ import annotations\n\nfrom factorio_circuit.blueprint.routing import RoutedWire, RoutingPlan\nfrom factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor\nfrom factorio_circuit.synthesis import incremental_joint_layout as incremental\nfrom factorio_circuit.synthesis import joint_layout as exact\nfrom factorio_circuit.synthesis import placement as base_placement\n\n\ndef _grid() -> base_placement._GridGeometry:\n    unit_x = tuple(float(x) for x in range(-3, 4))\n    unit_slots = tuple((x, 0.0) for x in unit_x)\n    return base_placement._GridGeometry(\n        slots=(),\n        unit_slots=unit_slots,\n        bounds=(-3.5, 3.5, -0.5, 0.5),\n        relay_forbidden_areas=(),\n        x_positions=(),\n        unit_x_positions=unit_x,\n        y_positions=(0.0,),\n    )\n\n\ndef _state(*, safe_span: float, blocker: bool = False):\n    entity_ids = (1, 2, 3) if blocker else (1, 2)\n    circuit = PhysicalCircuit(\n        "reach_backoff",\n        entities=[ConstantCombinator(entity_id) for entity_id in entity_ids],\n    )\n    positions = {1: (0.0, 0.0), 2: (2.0, 0.0)}\n    if blocker:\n        positions[3] = (-1.0, 0.0)\n    state = exact._JointState(\n        circuit=circuit,\n        endpoints_by_group={},\n        colors_by_group={},\n        positions=positions,\n        relay_positions={},\n        relay_groups={},\n        safe_span=safe_span,\n        forbidden_areas=(),\n    )\n    topology = incremental._FeasibleTopology.build(\n        state,\n        RoutingPlan(\n            relays=(),\n            wires=(RoutedWire(1, 1, 2, 1, WireColor.RED),),\n        ),\n    )\n    return state, topology\n\n\ndef _repair(state, topology, desired):\n    grid = _grid()\n    occupancy = incremental._SpatialOccupancy.build(state)\n    return incremental._reach_safe_backoff_target(\n        state,\n        topology,\n        1,\n        desired,\n        occupancy,\n        grid,\n        set(grid.unit_slots),\n        set(grid.slots),\n    )\n\n\ndef test_backoff_finds_shorter_reach_safe_step_without_rng() -> None:\n    state, topology = _state(safe_span=3.1)\n    desired = (-3.0, 0.0)\n    assert topology.proposal_delta(state, {1: desired}) is None\n\n    repaired = _repair(state, topology, desired)\n\n    assert repaired is not None\n    assert repaired not in {(0.0, 0.0), desired}\n    assert topology.proposal_delta(state, {1: repaired}) is not None\n\n\ndef test_backoff_returns_none_when_one_grid_step_already_breaks_reach() -> None:\n    state, topology = _state(safe_span=2.1)\n\n    assert _repair(state, topology, (-1.0, 0.0)) is None\n\n\ndef test_backoff_does_not_move_into_stationary_geometry() -> None:\n    state, topology = _state(safe_span=3.1, blocker=True)\n\n    assert _repair(state, topology, (-3.0, 0.0)) is None\n'''
)

roadmap = "docs/roadmap.md"
replace_once(
    roadmap,
    '''5. only after measurement, lower-level performance work in occupancy, routing indexes, and proposal evaluation.\n\n### C acceptance\n''',
    '''5. only after measurement, lower-level performance work in occupancy, routing indexes, and proposal evaluation.\n\n### Experiment record\n\n- **Rejected: adaptive coarse retopology.** In 12 paired runs it produced 0 better / 12 equal / 0 worse physical objectives while adding four rebuilds per run and increasing routing work/runtime.\n- **Rejected: terminal + one adjacent relay translation.** In 18 paired runs it produced 0 better / 18 equal / 0 worse objectives. It attempted 13,096 rescues and accepted none; taut safe-span chains simply moved the over-span violation to the relay's far side.\n- **Current: reach-safe proposal backoff.** When an ordinary non-swap proposal exceeds wire reach, try progressively shorter snapped steps along the same direction before counting a reach rejection. This adds no RNG calls and caps repair work at seven local feasibility checks.\n\n### C acceptance\n''',
)
replace_once(
    roadmap,
    '''Begin **Milestone C** with adaptive coarse retopology at the existing safe epoch boundary. Preserve the fixed-fraction rebuild schedule as a baseline fallback, add conservative triggers for sustained objective stagnation or wire-reach pressure, and keep a cooldown between adaptive rebuilds. Use the Milestone B counters and the Milestone A corpus to compare quality and routing work across seeds before accepting broader proposal or compound-move changes.''',
    '''Continue **Milestone C** with reach-safe proposal backoff. Compare the current proposal generator against deterministic shorter-step repair on identical seeds and budgets. Accept it only if it either improves the lexicographic physical objective or materially reduces wasted reach-rejection work without an offsetting runtime cost; otherwise remove it and proceed to a different proposal/local-repair strategy.''',
)
