from __future__ import annotations

from pathlib import Path


def replace_exact(path: str, old: str, new: str, *, count: int = 1) -> None:
    file = Path(path)
    text = file.read_text()
    actual = text.count(old)
    if actual != count:
        raise SystemExit(f"{path}: expected {count} copies, found {actual}")
    file.write_text(text.replace(old, new, count))


joint = "src/factorio_circuit/synthesis/incremental_joint_layout.py"
observed = "src/factorio_circuit/synthesis/layout_observability.py"
tests = "tests/synthesis/test_incremental_joint_regressions.py"

replace_exact(
    joint,
    "_TRACK_EXACT_ACCEPTED_MOVES = True\n",
    "_TRACK_EXACT_ACCEPTED_MOVES = True\n_FILTER_REACH_IMMOBILE_PROPOSALS = True\n",
)

helper = '''\n\ndef _has_reach_feasible_alternative(\n    state: exact._JointState,\n    topology: _FeasibleTopology,\n    object_id: int,\n    grid: base_placement._GridGeometry,\n) -> bool:\n    \"\"\"Return whether this object has any other lattice site that preserves current wire reach.\"\"\"\n\n    incident = topology.incident_wires.get(object_id, ())\n    if not incident:\n        return True\n    current = state.object_position(object_id)\n    if object_id in state.relay_positions:\n        candidates = grid.unit_slots\n    else:\n        candidates = base_placement._candidate_positions(\n            state.circuit.entity_by_id(object_id),\n            grid,\n        )\n\n    neighbors: list[Position] = []\n    for wire in incident:\n        remote, _connector = _remote_endpoint(wire, object_id)\n        if remote != object_id:\n            neighbors.append(state.object_position(remote))\n    if not neighbors:\n        return True\n\n    safe_span = state.safe_span\n    left = max(position[0] - safe_span for position in neighbors)\n    right = min(position[0] + safe_span for position in neighbors)\n    top = max(position[1] - safe_span for position in neighbors)\n    bottom = min(position[1] + safe_span for position in neighbors)\n    if left > right + _EPSILON or top > bottom + _EPSILON:\n        return False\n\n    for candidate in candidates:\n        if candidate == current:\n            continue\n        x, y = candidate\n        if (\n            x < left - _EPSILON\n            or x > right + _EPSILON\n            or y < top - _EPSILON\n            or y > bottom + _EPSILON\n        ):\n            continue\n        if all(_distance(candidate, neighbor) <= safe_span + _EPSILON for neighbor in neighbors):\n            return True\n    return False\n\n\ndef _reach_mobile_proposal_pool(\n    state: exact._JointState,\n    topology: _FeasibleTopology,\n    proposal_pool: list[int],\n    grid: base_placement._GridGeometry,\n) -> list[int]:\n    \"\"\"Drop objects whose current neighbors make every alternative lattice site over-span.\"\"\"\n\n    if not _FILTER_REACH_IMMOBILE_PROPOSALS:\n        return proposal_pool\n    return [\n        object_id\n        for object_id in proposal_pool\n        if _has_reach_feasible_alternative(state, topology, object_id, grid)\n    ]\n'''
replace_exact(joint, "\ndef _proposed_position(\n", helper + "\ndef _proposed_position(\n")

pool = '''        proposal_pool = (\n            implementation_outliers\n            or outliers\n            or (movable_entities if movable_entities else movable_relays)\n        )\n'''
replace_exact(
    joint,
    pool,
    pool
    + '''        proposal_pool = _reach_mobile_proposal_pool(\n            state,\n            topology,\n            proposal_pool,\n            grid,\n        )\n''',
)
replace_exact(
    joint,
    '''        if not proposal_pool:\n            continue\n\n        for step in range(epoch_start, epoch_end):\n''',
    '''        if not proposal_pool and not _FILTER_REACH_IMMOBILE_PROPOSALS:\n            continue\n\n        steps = range(epoch_start, epoch_end) if proposal_pool else ()\n        for step in steps:\n''',
)

replace_exact(
    observed,
    pool,
    pool
    + '''        proposal_pool = incremental._reach_mobile_proposal_pool(\n            state,\n            topology,\n            proposal_pool,\n            grid,\n        )\n''',
)
replace_exact(
    observed,
    '''        if not proposal_pool:\n            _complete_epoch(stats, best_score=best_score, improved=False)\n            continue\n\n        epoch_improved = False\n        for step in range(epoch_start, epoch_end):\n''',
    '''        if not proposal_pool and not incremental._FILTER_REACH_IMMOBILE_PROPOSALS:\n            _complete_epoch(stats, best_score=best_score, improved=False)\n            continue\n\n        epoch_improved = False\n        steps = range(epoch_start, epoch_end) if proposal_pool else ()\n        for step in steps:\n''',
)

Path(tests).write_text(
    Path(tests).read_text()
    + '''\n\ndef test_reach_mobility_detects_taut_and_slack_relay_domains() -> None:\n    options = PlacementOptions(\n        iterations=0,\n        reserve_corridors=False,\n        target_fill=0.6,\n    )\n    grid = incremental.base_placement._candidate_grid(4, 2, options)\n\n    taut = _constant_state_with_relay()\n    taut.safe_span = 1.5\n    taut_topology = incremental._FeasibleTopology.build(\n        taut,\n        RoutingPlan(\n            relays=(BlueprintRelay(3, (1.5, 0.0), \"relay\"),),\n            wires=(\n                RoutedWire(1, 1, 3, 1, WireColor.RED),\n                RoutedWire(3, 1, 2, 1, WireColor.RED),\n            ),\n        ),\n    )\n    assert not incremental._has_reach_feasible_alternative(taut, taut_topology, 3, grid)\n\n    slack = _constant_state_with_relay()\n    slack_topology = incremental._FeasibleTopology.build(\n        slack,\n        RoutingPlan(\n            relays=(BlueprintRelay(3, (1.5, 0.0), \"relay\"),),\n            wires=(\n                RoutedWire(1, 1, 3, 1, WireColor.RED),\n                RoutedWire(3, 1, 2, 1, WireColor.RED),\n            ),\n        ),\n    )\n    assert incremental._has_reach_feasible_alternative(slack, slack_topology, 3, grid)\n'''
)
