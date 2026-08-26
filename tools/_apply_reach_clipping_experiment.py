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
    "from math import ceil, exp, floor, hypot\n",
    "from bisect import bisect_left\nfrom math import ceil, exp, floor, hypot, sqrt\n",
)
helper = '''def _maximum_reach_safe_fraction(
    state: exact._JointState,
    topology: _FeasibleTopology,
    object_id: int,
    desired: Position,
) -> float:
    """Return the largest continuous fraction of one move that preserves incident-wire reach."""

    current = state.object_position(object_id)
    dx = desired[0] - current[0]
    dy = desired[1] - current[1]
    quadratic = dx * dx + dy * dy
    if quadratic <= _EPSILON:
        return 0.0

    maximum = 1.0
    radius_squared = state.safe_span * state.safe_span
    for wire in topology.incident_wires.get(object_id, ()):
        remote = wire.target_entity if wire.source_entity == object_id else wire.source_entity
        remote_position = state.object_position(remote)
        rx = current[0] - remote_position[0]
        ry = current[1] - remote_position[1]
        linear = 2.0 * (dx * rx + dy * ry)
        constant = rx * rx + ry * ry - radius_squared
        discriminant = max(0.0, linear * linear - 4.0 * quadratic * constant)
        positive_root = (-linear + sqrt(discriminant)) / (2.0 * quadratic)
        maximum = min(maximum, positive_root)
    return min(1.0, max(0.0, maximum))


def _bracketing_axis_values(values: tuple[float, ...], coordinate: float) -> tuple[float, ...]:
    """Return at most two sorted grid coordinates surrounding one continuous coordinate."""

    if not values:
        return ()
    index = bisect_left(values, coordinate)
    candidates: set[float] = set()
    if index < len(values):
        candidates.add(values[index])
    if index > 0:
        candidates.add(values[index - 1])
    return tuple(sorted(candidates))


def _reach_clipped_target(
    state: exact._JointState,
    topology: _FeasibleTopology,
    object_id: int,
    desired: Position,
    occupancy: _SpatialOccupancy,
    grid: base_placement._GridGeometry,
    unit_sites: set[Position],
    wide_sites: set[Position],
) -> Position | None:
    """Clip an over-reaching implementation proposal to its continuous reach boundary.

    Relay proposals are intentionally excluded: the relay corpus shows that taut chains rarely have
    a useful single-relay displacement. For an implementation entity, incident wire disks bound the
    same proposed direction analytically. Only the at-most-four grid sites bracketing that clipped
    continuous point are examined, so failed repair remains local and bounded.
    """

    if object_id in state.relay_positions:
        return None
    current = state.object_position(object_id)
    fraction = _maximum_reach_safe_fraction(state, topology, object_id, desired)
    if fraction <= _EPSILON or fraction >= 1.0 - _EPSILON:
        return None
    clipped = (
        current[0] + (desired[0] - current[0]) * fraction,
        current[1] + (desired[1] - current[1]) * fraction,
    )
    entity = state.circuit.entity_by_id(object_id)
    x_axis = grid.unit_x_positions if isinstance(entity, ConstantCombinator) else grid.x_positions
    x_values = _bracketing_axis_values(x_axis, clipped[0])
    y_values = _bracketing_axis_values(grid.y_positions, clipped[1])
    displacement = (desired[0] - current[0], desired[1] - current[1])
    candidates = sorted(
        ((x, y) for x in x_values for y in y_values),
        key=lambda position: (
            _distance(position, desired),
            -(
                (position[0] - current[0]) * displacement[0]
                + (position[1] - current[1]) * displacement[1]
            ),
            position,
        ),
    )
    for candidate in candidates:
        if candidate == current:
            continue
        progress = (
            (candidate[0] - current[0]) * displacement[0]
            + (candidate[1] - current[1]) * displacement[1]
        )
        if progress <= _EPSILON:
            continue
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
    '''            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None and other is None and object_id not in state.relay_positions:\n                clipped_target = _reach_clipped_target(\n                    state,\n                    topology,\n                    object_id,\n                    target,\n                    occupancy,\n                    grid,\n                    unit_sites,\n                    wide_sites,\n                )\n                if clipped_target is not None:\n                    target = clipped_target\n                    targets = {object_id: target}\n                    wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                continue\n''',
)

observability = "src/factorio_circuit/synthesis/layout_observability.py"
marker = "    swaps_accepted: int = 0\n    relay_deletions: int = 0\n"
replacement = (
    "    swaps_accepted: int = 0\n"
    "    reach_clip_attempts: int = 0\n"
    "    reach_clip_feasible_targets: int = 0\n"
    "    reach_clip_moves_accepted: int = 0\n"
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
    "            reach_clip_attempts=self.reach_clip_attempts,\n"
    "            reach_clip_feasible_targets=self.reach_clip_feasible_targets,\n"
    "            reach_clip_moves_accepted=self.reach_clip_moves_accepted,\n"
    "            relay_deletions=self.relay_deletions,\n",
)
replace_once(
    observability,
    '''            wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                stats.wire_reach_rejections += 1\n                continue\n''',
    '''            wire_delta = topology.proposal_delta(state, targets)\n            clip_used = False\n            if wire_delta is None and other is None and object_id not in state.relay_positions:\n                stats.reach_clip_attempts += 1\n                clipped_target = incremental._reach_clipped_target(\n                    state,\n                    topology,\n                    object_id,\n                    target,\n                    occupancy,\n                    grid,\n                    unit_sites,\n                    wide_sites,\n                )\n                if clipped_target is not None:\n                    stats.reach_clip_feasible_targets += 1\n                    clip_used = True\n                    target = clipped_target\n                    targets = {object_id: target}\n                    wire_delta = topology.proposal_delta(state, targets)\n            if wire_delta is None:\n                stats.wire_reach_rejections += 1\n                continue\n''',
)
replace_once(
    observability,
    '''            if other is not None:\n                stats.swaps_accepted += 1\n\n        topology = incremental._simplify_feasible_topology(state, topology)\n''',
    '''            if other is not None:\n                stats.swaps_accepted += 1\n            if clip_used:\n                stats.reach_clip_moves_accepted += 1\n\n        topology = incremental._simplify_feasible_topology(state, topology)\n''',
)

benchmark = "benchmarks/layout_optimizer_observability.py"
replace_once(
    benchmark,
    '''        f"proposal-kind(implementation={stats.implementation_proposals}, "\n        f"relay={stats.relay_proposals}), swaps={stats.swaps_accepted}/{stats.swap_attempts}, "\n''',
    '''        f"proposal-kind(implementation={stats.implementation_proposals}, "\n        f"relay={stats.relay_proposals}), swaps={stats.swaps_accepted}/{stats.swap_attempts}, "\n        f"reach-clip={stats.reach_clip_moves_accepted}/"\n        f"{stats.reach_clip_feasible_targets}/{stats.reach_clip_attempts}, "\n''',
)

test_observability = "tests/synthesis/test_layout_observability.py"
replace_once(
    test_observability,
    "    assert stats.swaps_accepted <= stats.swap_attempts\n",
    "    assert stats.swaps_accepted <= stats.swap_attempts\n"
    "    assert stats.reach_clip_moves_accepted <= stats.reach_clip_feasible_targets\n"
    "    assert stats.reach_clip_feasible_targets <= stats.reach_clip_attempts\n",
)

Path("tests/synthesis/test_reach_clipping.py").write_text(
    '''from __future__ import annotations\n\nimport pytest\n\nfrom factorio_circuit.blueprint.routing import BlueprintRelay, RoutedWire, RoutingPlan\nfrom factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor\nfrom factorio_circuit.synthesis import incremental_joint_layout as incremental\nfrom factorio_circuit.synthesis import joint_layout as exact\nfrom factorio_circuit.synthesis import placement as base_placement\n\n\ndef _grid() -> base_placement._GridGeometry:\n    unit_x = tuple(float(x) for x in range(-3, 4))\n    unit_slots = tuple((x, 0.0) for x in unit_x)\n    return base_placement._GridGeometry(\n        slots=(),\n        unit_slots=unit_slots,\n        bounds=(-3.5, 3.5, -0.5, 0.5),\n        relay_forbidden_areas=(),\n        x_positions=(),\n        unit_x_positions=unit_x,\n        y_positions=(0.0,),\n    )\n\n\ndef _state(*, safe_span: float, blocker: bool = False):\n    entity_ids = (1, 2, 3) if blocker else (1, 2)\n    circuit = PhysicalCircuit(\n        "reach_clipping",\n        entities=[ConstantCombinator(entity_id) for entity_id in entity_ids],\n    )\n    positions = {1: (0.0, 0.0), 2: (2.0, 0.0)}\n    if blocker:\n        positions[3] = (-1.0, 0.0)\n    state = exact._JointState(\n        circuit=circuit,\n        endpoints_by_group={},\n        colors_by_group={},\n        positions=positions,\n        relay_positions={},\n        relay_groups={},\n        safe_span=safe_span,\n        forbidden_areas=(),\n    )\n    topology = incremental._FeasibleTopology.build(\n        state,\n        RoutingPlan(\n            relays=(),\n            wires=(RoutedWire(1, 1, 2, 1, WireColor.RED),),\n        ),\n    )\n    return state, topology\n\n\ndef _clip(state, topology, desired):\n    grid = _grid()\n    occupancy = incremental._SpatialOccupancy.build(state)\n    return incremental._reach_clipped_target(\n        state,\n        topology,\n        1,\n        desired,\n        occupancy,\n        grid,\n        set(grid.unit_slots),\n        set(grid.slots),\n    )\n\n\ndef test_continuous_fraction_matches_incident_wire_boundary() -> None:\n    state, topology = _state(safe_span=3.1)\n\n    fraction = incremental._maximum_reach_safe_fraction(state, topology, 1, (-3.0, 0.0))\n\n    assert fraction == pytest.approx(1.1 / 3.0)\n\n\ndef test_clipping_finds_furthest_bracketing_reach_safe_grid_step() -> None:\n    state, topology = _state(safe_span=3.1)\n    desired = (-3.0, 0.0)\n    assert topology.proposal_delta(state, {1: desired}) is None\n\n    clipped = _clip(state, topology, desired)\n\n    assert clipped == (-1.0, 0.0)\n    assert topology.proposal_delta(state, {1: clipped}) is not None\n\n\ndef test_clipping_returns_none_when_no_forward_grid_step_is_reachable() -> None:\n    state, topology = _state(safe_span=2.1)\n\n    assert _clip(state, topology, (-3.0, 0.0)) is None\n\n\ndef test_clipping_does_not_move_into_stationary_geometry() -> None:\n    state, topology = _state(safe_span=3.1, blocker=True)\n\n    assert _clip(state, topology, (-3.0, 0.0)) is None\n\n\ndef test_clipping_skips_relay_objects() -> None:\n    circuit = PhysicalCircuit("relay_clip", entities=[ConstantCombinator(1)])\n    state = exact._JointState(\n        circuit=circuit,\n        endpoints_by_group={},\n        colors_by_group={},\n        positions={1: (2.0, 0.0)},\n        relay_positions={2: (0.0, 0.0)},\n        relay_groups={2: frozenset()},\n        safe_span=3.1,\n        forbidden_areas=(),\n    )\n    topology = incremental._FeasibleTopology.build(\n        state,\n        RoutingPlan(\n            relays=(BlueprintRelay(2, (0.0, 0.0), "relay"),),\n            wires=(RoutedWire(2, 1, 1, 1, WireColor.RED),),\n        ),\n    )\n    grid = _grid()\n\n    clipped = incremental._reach_clipped_target(\n        state,\n        topology,\n        2,\n        (-3.0, 0.0),\n        incremental._SpatialOccupancy.build(state),\n        grid,\n        set(grid.unit_slots),\n        set(grid.slots),\n    )\n\n    assert clipped is None\n'''
)

roadmap = "docs/roadmap.md"
replace_once(
    roadmap,
    '''5. only after measurement, lower-level performance work in occupancy, routing indexes, and proposal evaluation.\n\n### C acceptance\n''',
    '''5. only after measurement, lower-level performance work in occupancy, routing indexes, and proposal evaluation.\n\n### Experiment record\n\n- **Rejected: adaptive coarse retopology.** In 12 paired runs it produced 0 better / 12 equal / 0 worse physical objectives while adding four rebuilds per run and increasing routing work/runtime.\n- **Rejected: terminal + one adjacent relay translation.** In 18 paired runs it produced 0 better / 18 equal / 0 worse objectives. It attempted 13,096 rescues and accepted none; taut safe-span chains simply moved the over-span violation to the relay's far side.\n- **Rejected: seven-step reach backoff.** It produced 0 better / 17 equal / 1 worse objectives. It reduced reach rejections on some unconstrained cases but added 7-18% runtime there and 2.6-4.5x runtime on taut/fixed cases.\n- **Current: analytical implementation reach clipping.** Intersect incident-wire reach disks along the original proposal direction, then test only the grid sites bracketing the resulting continuous boundary. Relay proposals are excluded.\n\n### C acceptance\n''',
)
replace_once(
    roadmap,
    '''Begin **Milestone C** with adaptive coarse retopology at the existing safe epoch boundary. Preserve the fixed-fraction rebuild schedule as a baseline fallback, add conservative triggers for sustained objective stagnation or wire-reach pressure, and keep a cooldown between adaptive rebuilds. Use the Milestone B counters and the Milestone A corpus to compare quality and routing work across seeds before accepting broader proposal or compound-move changes.''',
    '''Continue **Milestone C** with analytical implementation reach clipping. Compare it against the current proposal generator on identical seeds and budgets. Accept it only if it improves the lexicographic physical objective or materially reduces wasted reach rejection without the broad runtime penalty observed in discrete backoff; otherwise remove it and move on to proposal-pool/local-repair strategies.''',
)
