from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement target, found {count}")
    file.write_text(text.replace(old, new, 1))


def replace_exactly(path: str, old: str, new: str, expected: int) -> None:
    file = Path(path)
    text = file.read_text()
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} replacement targets, found {count}")
    file.write_text(text.replace(old, new))


incremental = "src/factorio_circuit/synthesis/incremental_joint_layout.py"
observability = "src/factorio_circuit/synthesis/layout_observability.py"
benchmark = "benchmarks/layout_optimizer_observability.py"
observability_test = "tests/synthesis/test_layout_observability.py"

helper = '''\
def _terminal_relay_compound_targets(
    state: exact._JointState,
    topology: _FeasibleTopology,
    object_id: int,
    target: Position,
    occupancy: _SpatialOccupancy,
    unit_sites: set[Position],
) -> dict[int, Position] | None:
    """Translate reach-blocking adjacent relays with one implementation terminal.

    This is a rescue for an otherwise wire-reach-rejected implementation move, not a new random
    proposal. Only directly incident movable relays whose current positions would become over-span
    move with the terminal. Every moved relay receives the terminal's exact displacement, and the
    full affected topology is revalidated before the transaction can be accepted.
    """

    if object_id in state.relay_positions:
        return None
    current = state.positions[object_id]
    displacement = (target[0] - current[0], target[1] - current[1])
    offending_relays: set[int] = set()
    for wire in topology.incident_wires.get(object_id, ()):
        remote = wire.target_entity if wire.source_entity == object_id else wire.source_entity
        if _distance(target, state.object_position(remote)) <= state.safe_span + _EPSILON:
            continue
        if remote not in state.relay_positions or remote in state.fixed_objects:
            return None
        offending_relays.add(remote)
    if not offending_relays:
        return None

    targets = {object_id: target}
    for relay_id in sorted(offending_relays):
        current_relay = state.relay_positions[relay_id]
        relay_target = (
            current_relay[0] + displacement[0],
            current_relay[1] + displacement[1],
        )
        if relay_target not in unit_sites:
            return None
        targets[relay_id] = relay_target

    moving = set(targets)
    for moving_id, moving_target in targets.items():
        if occupancy.overlaps(moving_id, moving_target, ignored=moving):
            return None

    moving_ids = sorted(targets)
    for index, left_id in enumerate(moving_ids):
        for right_id in moving_ids[index + 1 :]:
            if base_placement._boxes_overlap(
                targets[left_id],
                state.object_half_extent(left_id),
                targets[right_id],
                state.object_half_extent(right_id),
            ):
                return None

    if topology.proposal_delta(state, targets) is None:
        return None
    return targets


def _apply_compound_targets(
    state: exact._JointState,
    occupancy: _SpatialOccupancy,
    targets: dict[int, Position],
) -> None:
    """Apply one already-validated multi-object translation transaction."""

    previous = {object_id: state.object_position(object_id) for object_id in targets}
    for object_id, position in previous.items():
        occupancy.remove(object_id, position)
    for object_id, position in targets.items():
        if object_id in state.relay_positions:
            state.relay_positions[object_id] = position
        else:
            state.positions[object_id] = position
    for object_id, position in targets.items():
        occupancy.add(object_id, position)


'''
replace_once(incremental, "def _anneal_feasible(\n", helper + "def _anneal_feasible(\n")

replace_once(
    incremental,
    '''\
            wire_delta = topology.proposal_delta(state, targets)
            if wire_delta is None:
                continue
''',
    '''\
            wire_delta = topology.proposal_delta(state, targets)
            compound_targets: dict[int, Position] | None = None
            if wire_delta is None and other is None and object_id not in state.relay_positions:
                compound_targets = _terminal_relay_compound_targets(
                    state,
                    topology,
                    object_id,
                    target,
                    occupancy,
                    unit_sites,
                )
                if compound_targets is not None:
                    targets = compound_targets
                    wire_delta = topology.proposal_delta(state, targets)
            if wire_delta is None:
                continue
''',
)

replace_once(
    incremental,
    '''\
            occupancy.remove(object_id, current)
            if other is not None:
                occupancy.remove(other, target)
            exact._apply_move(state, object_id, target, other)
            occupancy.add(object_id, target)
            if other is not None:
                occupancy.add(other, current)
            topology.total_energy += wire_delta
''',
    '''\
            if compound_targets is None:
                occupancy.remove(object_id, current)
                if other is not None:
                    occupancy.remove(other, target)
                exact._apply_move(state, object_id, target, other)
                occupancy.add(object_id, target)
                if other is not None:
                    occupancy.add(other, current)
            else:
                _apply_compound_targets(state, occupancy, compound_targets)
            topology.total_energy += wire_delta
''',
)

replace_exactly(
    observability,
    "    swaps_accepted: int = 0\n    relay_deletions: int = 0\n",
    "    swaps_accepted: int = 0\n"
    "    compound_move_attempts: int = 0\n"
    "    compound_moves_accepted: int = 0\n"
    "    compound_relays_moved: int = 0\n"
    "    relay_deletions: int = 0\n",
    2,
)
replace_once(
    observability,
    "            swaps_accepted=self.swaps_accepted,\n            relay_deletions=self.relay_deletions,\n",
    "            swaps_accepted=self.swaps_accepted,\n"
    "            compound_move_attempts=self.compound_move_attempts,\n"
    "            compound_moves_accepted=self.compound_moves_accepted,\n"
    "            compound_relays_moved=self.compound_relays_moved,\n"
    "            relay_deletions=self.relay_deletions,\n",
)

replace_once(
    observability,
    '''\
            wire_delta = topology.proposal_delta(state, targets)
            if wire_delta is None:
                stats.wire_reach_rejections += 1
                continue
''',
    '''\
            wire_delta = topology.proposal_delta(state, targets)
            compound_targets: dict[int, base_placement.Position] | None = None
            if wire_delta is None and other is None and not selected_is_relay:
                stats.compound_move_attempts += 1
                compound_targets = incremental._terminal_relay_compound_targets(
                    state,
                    topology,
                    object_id,
                    target,
                    occupancy,
                    unit_sites,
                )
                if compound_targets is not None:
                    targets = compound_targets
                    wire_delta = topology.proposal_delta(state, targets)
            if wire_delta is None:
                stats.wire_reach_rejections += 1
                continue
''',
)

replace_once(
    observability,
    '''\
            occupancy.remove(object_id, current)
            if other is not None:
                occupancy.remove(other, target)
            exact._apply_move(state, object_id, target, other)
            occupancy.add(object_id, target)
            if other is not None:
                occupancy.add(other, current)
            topology.total_energy += wire_delta
            stats.accepted_moves += 1
''',
    '''\
            if compound_targets is None:
                occupancy.remove(object_id, current)
                if other is not None:
                    occupancy.remove(other, target)
                exact._apply_move(state, object_id, target, other)
                occupancy.add(object_id, target)
                if other is not None:
                    occupancy.add(other, current)
            else:
                incremental._apply_compound_targets(state, occupancy, compound_targets)
            topology.total_energy += wire_delta
            stats.accepted_moves += 1
            if compound_targets is not None:
                stats.compound_moves_accepted += 1
                stats.compound_relays_moved += len(compound_targets) - 1
''',
)

replace_once(
    benchmark,
    '''\
        f"relay={stats.relay_proposals}), swaps={stats.swaps_accepted}/{stats.swap_attempts}, "
        f"simplify(calls={stats.simplification_calls}, total={stats.relay_deletions}, "
''',
    '''\
        f"relay={stats.relay_proposals}), swaps={stats.swaps_accepted}/{stats.swap_attempts}, "
        f"compound={stats.compound_moves_accepted}/{stats.compound_move_attempts} "
        f"relays-moved={stats.compound_relays_moved}, "
        f"simplify(calls={stats.simplification_calls}, total={stats.relay_deletions}, "
''',
)

replace_once(
    observability_test,
    '''\
    assert stats.swaps_accepted <= stats.swap_attempts
    assert stats.classified_relay_deletions == stats.relay_deletions
''',
    '''\
    assert stats.swaps_accepted <= stats.swap_attempts
    assert stats.compound_moves_accepted <= stats.compound_move_attempts
    assert stats.compound_relays_moved >= stats.compound_moves_accepted
    assert stats.classified_relay_deletions == stats.relay_deletions
''',
)

Path("tests/synthesis/test_compound_relay_moves.py").write_text(
    '''from __future__ import annotations

from factorio_circuit.blueprint.routing import BlueprintRelay, RoutedWire, RoutingPlan
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact


def _compound_state(
    *,
    right_x: float = 3.0,
    fixed_relay: bool = False,
    blocker: bool = False,
) -> tuple[exact._JointState, incremental._FeasibleTopology]:
    entity_ids = (1, 2, 4) if blocker else (1, 2)
    circuit = PhysicalCircuit(
        "compound_relay_move",
        entities=[ConstantCombinator(entity_id) for entity_id in entity_ids],
    )
    positions = {1: (0.0, 0.0), 2: (right_x, 0.0)}
    if blocker:
        positions[4] = (1.0, 0.0)
    state = exact._JointState(
        circuit=circuit,
        endpoints_by_group={
            1: (
                abstract.Endpoint(1, abstract.Connector.SINGLE),
                abstract.Endpoint(2, abstract.Connector.SINGLE),
            )
        },
        colors_by_group={1: WireColor.RED},
        positions=positions,
        relay_positions={3: (2.0, 0.0)},
        relay_groups={3: frozenset({1})},
        safe_span=2.1,
        forbidden_areas=(),
        fixed_objects=frozenset({3}) if fixed_relay else frozenset(),
    )
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(
            relays=(BlueprintRelay(3, (2.0, 0.0), "relay"),),
            wires=(
                RoutedWire(1, 1, 3, 1, WireColor.RED),
                RoutedWire(3, 1, 2, 1, WireColor.RED),
            ),
        ),
    )
    return state, topology


def _compound_targets(
    state: exact._JointState,
    topology: incremental._FeasibleTopology,
) -> tuple[dict[int, tuple[float, float]] | None, incremental._SpatialOccupancy]:
    occupancy = incremental._SpatialOccupancy.build(state)
    targets = incremental._terminal_relay_compound_targets(
        state,
        topology,
        1,
        (-1.0, 0.0),
        occupancy,
        {(-1.0, 0.0), (0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)},
    )
    return targets, occupancy


def test_terminal_move_can_translate_one_adjacent_relay_transactionally() -> None:
    state, topology = _compound_state()
    targets, occupancy = _compound_targets(state, topology)

    assert targets == {1: (-1.0, 0.0), 3: (1.0, 0.0)}
    assert topology.proposal_delta(state, targets) is not None

    incremental._apply_compound_targets(state, occupancy, targets)

    assert state.positions[1] == (-1.0, 0.0)
    assert state.relay_positions[3] == (1.0, 0.0)
    assert topology.proposal_delta(state, {}) == 0.0


def test_compound_move_does_not_translate_fixed_relay() -> None:
    state, topology = _compound_state(fixed_relay=True)
    targets, _occupancy = _compound_targets(state, topology)

    assert targets is None


def test_compound_move_rejects_when_relay_far_side_would_break_reach() -> None:
    state, topology = _compound_state(right_x=4.0)
    targets, _occupancy = _compound_targets(state, topology)

    assert targets is None


def test_compound_move_rejects_stationary_geometry_at_relay_target() -> None:
    state, topology = _compound_state(blocker=True)
    targets, _occupancy = _compound_targets(state, topology)

    assert targets is None
'''
)
