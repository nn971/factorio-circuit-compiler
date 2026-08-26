from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one match in {path}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1))


incremental = "src/factorio_circuit/synthesis/incremental_joint_layout.py"
replace_once(
    incremental,
    "from heapq import heappop, heappush\n",
    "from heapq import heapify, heappop, heappush\n",
)
replace_once(
    incremental,
    "_EXACT_BEST_ACCEPTED_STRIDE = 32\n",
    "_TRACK_EXACT_ACCEPTED_MOVES = True\n",
)

tracker = '''@dataclass(slots=True)
class _ExactObjectiveTracker:
    """Incrementally maintain the public lexicographic objective inside one topology epoch."""

    bounds: dict[int, tuple[float, float, float, float]]
    left_heap: list[tuple[float, int]]
    right_heap: list[tuple[float, int]]
    top_heap: list[tuple[float, int]]
    bottom_heap: list[tuple[float, int]]
    wire_length: float

    @classmethod
    def build(
        cls,
        state: exact._JointState,
        topology: _FeasibleTopology,
    ) -> _ExactObjectiveTracker:
        bounds: dict[int, tuple[float, float, float, float]] = {}
        for object_id in [*state.positions, *state.relay_positions]:
            x, y = state.object_position(object_id)
            half_x, half_y = state.object_half_extent(object_id)
            bounds[object_id] = (x - half_x, x + half_x, y - half_y, y + half_y)
        left_heap = [(value[0], object_id) for object_id, value in bounds.items()]
        right_heap = [(-value[1], object_id) for object_id, value in bounds.items()]
        top_heap = [(value[2], object_id) for object_id, value in bounds.items()]
        bottom_heap = [(-value[3], object_id) for object_id, value in bounds.items()]
        heapify(left_heap)
        heapify(right_heap)
        heapify(top_heap)
        heapify(bottom_heap)
        wire_length = sum(
            _distance(
                state.object_position(wire.source_entity),
                state.object_position(wire.target_entity),
            )
            for wire in topology.routing.wires
        )
        return cls(
            bounds,
            left_heap,
            right_heap,
            top_heap,
            bottom_heap,
            wire_length,
        )

    def proposal_wire_length_delta(
        self,
        state: exact._JointState,
        topology: _FeasibleTopology,
        targets: dict[int, Position],
    ) -> float:
        affected: set[wire_routing.RoutedWire] = set()
        for object_id in targets:
            affected.update(topology.incident_wires.get(object_id, ()))
        delta = 0.0
        for wire in affected:
            source_before = state.object_position(wire.source_entity)
            target_before = state.object_position(wire.target_entity)
            source_after = targets.get(wire.source_entity, source_before)
            target_after = targets.get(wire.target_entity, target_before)
            delta += _distance(source_after, target_after)
            delta -= _distance(source_before, target_before)
        return delta

    def accept_move(
        self,
        state: exact._JointState,
        targets: dict[int, Position],
        wire_length_delta: float,
    ) -> None:
        self.wire_length += wire_length_delta
        for object_id in targets:
            x, y = state.object_position(object_id)
            half_x, half_y = state.object_half_extent(object_id)
            bounds = (x - half_x, x + half_x, y - half_y, y + half_y)
            self.bounds[object_id] = bounds
            heappush(self.left_heap, (bounds[0], object_id))
            heappush(self.right_heap, (-bounds[1], object_id))
            heappush(self.top_heap, (bounds[2], object_id))
            heappush(self.bottom_heap, (-bounds[3], object_id))

    def _clean_heap(
        self,
        heap: list[tuple[float, int]],
        *,
        bound_index: int,
        sign: float,
    ) -> None:
        while heap:
            stored, object_id = heap[0]
            bounds = self.bounds.get(object_id)
            if bounds is not None and stored == sign * bounds[bound_index]:
                return
            heappop(heap)

    def score(self, state: exact._JointState) -> tuple[int, float, float]:
        if not self.bounds:
            return (len(state.relay_positions), 0.0, self.wire_length)
        self._clean_heap(self.left_heap, bound_index=0, sign=1.0)
        self._clean_heap(self.right_heap, bound_index=1, sign=-1.0)
        self._clean_heap(self.top_heap, bound_index=2, sign=1.0)
        self._clean_heap(self.bottom_heap, bound_index=3, sign=-1.0)
        left = self.left_heap[0][0]
        right = -self.right_heap[0][0]
        top = self.top_heap[0][0]
        bottom = -self.bottom_heap[0][0]
        return (
            len(state.relay_positions),
            (right - left) * (bottom - top),
            self.wire_length,
        )


'''
replace_once(
    incremental,
    "@dataclass(slots=True)\nclass _SpatialOccupancy:\n",
    tracker + "@dataclass(slots=True)\nclass _SpatialOccupancy:\n",
)
replace_once(
    incremental,
    '''def _accepted_move_exact_score(
    state: exact._JointState,
    topology: _FeasibleTopology,
    center: Position,
) -> tuple[int, float, float]:
    """Measure the public lexicographic objective after one accepted hot-loop move."""

    return _exact_score(state, topology, center)
''',
    '''def _accepted_move_exact_score(
    state: exact._JointState,
    topology: _FeasibleTopology,
    center: Position,
    tracker: _ExactObjectiveTracker,
) -> tuple[int, float, float]:
    """Read the exact public objective from local accepted-move bookkeeping."""

    _ = topology, center
    return tracker.score(state)
''',
)
replace_once(
    incremental,
    '''    best_relay_groups = dict(state.relay_groups)
    best_routing = topology.routing
    topology_rebuilds = {
''',
    '''    best_relay_groups = dict(state.relay_groups)
    best_routing = topology.routing
    exact_tracker = (
        _ExactObjectiveTracker.build(state, topology) if _TRACK_EXACT_ACCEPTED_MOVES else None
    )
    topology_rebuilds = {
''',
)
replace_once(
    incremental,
    '''        accepted_since_exact = 0
        for step in range(epoch_start, epoch_end):
''',
    '''        for step in range(epoch_start, epoch_end):
''',
)
replace_once(
    incremental,
    '''            if wire_delta is None:
                continue

            compact_delta = sum(
''',
    '''            if wire_delta is None:
                continue
            exact_wire_delta = (
                exact_tracker.proposal_wire_length_delta(state, topology, targets)
                if exact_tracker is not None
                else 0.0
            )

            compact_delta = sum(
''',
)
replace_once(
    incremental,
    '''            topology.total_energy += wire_delta

            accepted_since_exact += 1
            if accepted_since_exact >= _EXACT_BEST_ACCEPTED_STRIDE:
                accepted_since_exact = 0
                accepted_score = _accepted_move_exact_score(state, topology, center)
                if accepted_score < best_score:
                    best_score = accepted_score
                    best_positions = dict(state.positions)
                    best_relays = dict(state.relay_positions)
                    best_relay_groups = dict(state.relay_groups)
                    best_routing = topology.routing
''',
    '''            topology.total_energy += wire_delta

            if exact_tracker is not None:
                exact_tracker.accept_move(state, targets, exact_wire_delta)
                accepted_score = _accepted_move_exact_score(
                    state,
                    topology,
                    center,
                    exact_tracker,
                )
                if accepted_score < best_score:
                    best_score = accepted_score
                    best_positions = dict(state.positions)
                    best_relays = dict(state.relay_positions)
                    best_relay_groups = dict(state.relay_groups)
                    best_routing = topology.routing
''',
)
replace_once(
    incremental,
    '''        score = _exact_score(state, topology, center)
        if score < best_score:
            best_score = score
            best_positions = dict(state.positions)
            best_relays = dict(state.relay_positions)
            best_relay_groups = dict(state.relay_groups)
            best_routing = topology.routing
''',
    '''        score = _exact_score(state, topology, center)
        if score < best_score:
            best_score = score
            best_positions = dict(state.positions)
            best_relays = dict(state.relay_positions)
            best_relay_groups = dict(state.relay_groups)
            best_routing = topology.routing
        if exact_tracker is not None:
            exact_tracker = _ExactObjectiveTracker.build(state, topology)
''',
)

observability = "src/factorio_circuit/synthesis/layout_observability.py"
replace_once(
    observability,
    '''    best_relay_groups = dict(state.relay_groups)
    best_routing = topology.routing
    topology_rebuilds = {
''',
    '''    best_relay_groups = dict(state.relay_groups)
    best_routing = topology.routing
    exact_tracker = (
        incremental._ExactObjectiveTracker.build(state, topology)
        if incremental._TRACK_EXACT_ACCEPTED_MOVES
        else None
    )
    topology_rebuilds = {
''',
)
replace_once(
    observability,
    '''        epoch_improved = False
        accepted_since_exact = 0
        for step in range(epoch_start, epoch_end):
''',
    '''        epoch_improved = False
        for step in range(epoch_start, epoch_end):
''',
)
replace_once(
    observability,
    '''            if wire_delta is None:
                stats.wire_reach_rejections += 1
                continue

            compact_delta = sum(
''',
    '''            if wire_delta is None:
                stats.wire_reach_rejections += 1
                continue
            exact_wire_delta = (
                exact_tracker.proposal_wire_length_delta(state, topology, targets)
                if exact_tracker is not None
                else 0.0
            )

            compact_delta = sum(
''',
)
replace_once(
    observability,
    '''            accepted_since_exact += 1
            if accepted_since_exact >= incremental._EXACT_BEST_ACCEPTED_STRIDE:
                accepted_since_exact = 0
                accepted_score = incremental._accepted_move_exact_score(state, topology, center)
                if accepted_score < best_score:
                    best_score = accepted_score
                    best_positions = dict(state.positions)
                    best_relays = dict(state.relay_positions)
                    best_relay_groups = dict(state.relay_groups)
                    best_routing = topology.routing
                    epoch_improved = True
''',
    '''            if exact_tracker is not None:
                exact_tracker.accept_move(state, targets, exact_wire_delta)
                accepted_score = incremental._accepted_move_exact_score(
                    state,
                    topology,
                    center,
                    exact_tracker,
                )
                if accepted_score < best_score:
                    best_score = accepted_score
                    best_positions = dict(state.positions)
                    best_relays = dict(state.relay_positions)
                    best_relay_groups = dict(state.relay_groups)
                    best_routing = topology.routing
                    epoch_improved = True
''',
)
replace_once(
    observability,
    '''        if score < best_score:
            best_score = score
            best_positions = dict(state.positions)
            best_relays = dict(state.relay_positions)
            best_relay_groups = dict(state.relay_groups)
            best_routing = topology.routing
            epoch_improved = True
        _complete_epoch(stats, best_score=best_score, improved=epoch_improved)
''',
    '''        if score < best_score:
            best_score = score
            best_positions = dict(state.positions)
            best_relays = dict(state.relay_positions)
            best_relay_groups = dict(state.relay_groups)
            best_routing = topology.routing
            epoch_improved = True
        if exact_tracker is not None:
            exact_tracker = incremental._ExactObjectiveTracker.build(state, topology)
        _complete_epoch(stats, best_score=best_score, improved=epoch_improved)
''',
)

tests = "tests/synthesis/test_incremental_joint_regressions.py"
test_file = Path(tests)
text = test_file.read_text()
marker = "\ndef test_annealer_retains_exact_best_state_seen_inside_an_epoch(\n"
if text.count(marker) != 1:
    raise RuntimeError("expected one exact-best regression marker")
text = text[: text.index(marker)]
text += r'''

def test_incremental_exact_objective_tracker_matches_full_score_after_moves() -> None:
    state = _constant_state_with_relay()
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(
            relays=(BlueprintRelay(3, (1.5, 0.0), "relay"),),
            wires=(
                RoutedWire(1, 1, 3, 1, WireColor.RED),
                RoutedWire(3, 1, 2, 1, WireColor.RED),
            ),
        ),
    )
    tracker = incremental._ExactObjectiveTracker.build(state, topology)
    center = (0.0, 0.0)

    assert tracker.score(state) == pytest.approx(incremental._exact_score(state, topology, center))

    targets = {1: (-1.0, 0.0)}
    wire_delta = tracker.proposal_wire_length_delta(state, topology, targets)
    exact._apply_move(state, 1, targets[1], None)
    tracker.accept_move(state, targets, wire_delta)

    assert tracker.score(state) == pytest.approx(incremental._exact_score(state, topology, center))

    targets = {1: (3.0, 0.0), 2: (-1.0, 0.0)}
    wire_delta = tracker.proposal_wire_length_delta(state, topology, targets)
    exact._apply_move(state, 1, targets[1], 2)
    tracker.accept_move(state, targets, wire_delta)

    assert tracker.score(state) == pytest.approx(incremental._exact_score(state, topology, center))


def test_annealer_retains_exact_best_state_seen_inside_an_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = PhysicalCircuit(
        "mid_epoch_best",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2)],
    )
    state = exact._JointState(
        circuit=physical,
        endpoints_by_group={},
        colors_by_group={},
        positions={1: (0.0, 0.0), 2: (10.0, 0.0)},
        relay_positions={},
        relay_groups={},
        safe_span=100.0,
        forbidden_areas=(),
    )
    topology = incremental._FeasibleTopology.build(
        state,
        RoutingPlan(relays=(), wires=()),
    )
    options = PlacementOptions(
        anchor_io=False,
        iterations=2,
        reserve_corridors=False,
        target_fill=0.6,
    )
    grid = incremental.base_placement._candidate_grid(8, 1, options)
    proposed = iter(((1.0, 0.0), (2.0, 0.0)))
    accepted_scores = iter(((0, 50.0, 50.0), (0, 80.0, 80.0)))
    first_accepted_positions: dict[int, tuple[float, float]] = {}

    monkeypatch.setattr(
        incremental,
        "_proposed_position",
        lambda *_args, **_kwargs: next(proposed),
    )
    monkeypatch.setattr(incremental, "_position_is_legal", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(incremental, "_rectangle_overflow", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(exact, "_compactness", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(incremental, "_TRACK_EXACT_ACCEPTED_MOVES", True)

    initial_calls = 0

    def exact_score(
        _state: exact._JointState,
        _topology: incremental._FeasibleTopology,
        _center: tuple[float, float],
    ) -> tuple[int, float, float]:
        nonlocal initial_calls
        initial_calls += 1
        return (0, 100.0, 100.0) if initial_calls == 1 else (0, 80.0, 80.0)

    def accepted_score(
        observed_state: exact._JointState,
        _topology: incremental._FeasibleTopology,
        _center: tuple[float, float],
        _tracker: incremental._ExactObjectiveTracker,
    ) -> tuple[int, float, float]:
        score = next(accepted_scores)
        if score == (0, 50.0, 50.0):
            first_accepted_positions.update(observed_state.positions)
        return score

    monkeypatch.setattr(incremental, "_exact_score", exact_score)
    monkeypatch.setattr(incremental, "_accepted_move_exact_score", accepted_score)

    incremental._anneal_feasible(state, topology, options, grid)

    assert first_accepted_positions
    assert state.positions == first_accepted_positions
'''
test_file.write_text(text)

benchmark = "benchmarks/layout_optimizer_exact_best_compare.py"
Path(benchmark).write_text(r'''"""Compare epoch-only best tracking with local exact accepted-move tracking."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from statistics import median
from time import perf_counter
from typing import Any

from benchmarks.layout_optimizer_corpus import (
    _fixed_endpoint_span_case,
    _narrow_corridor_case,
    _perimeter_anchor_case,
    _relay_forest_case,
    _shared_bus_case,
)
from benchmarks.layout_optimizer_topology_corpus import _clustered_sparse_cut_case
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis.layout_observability import optimize_physical_layout_observed
from factorio_circuit.synthesis.layout_optimizer import validate_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions

CaseFactory = Callable[[], Any]
_TRAJECTORY_FIELDS = (
    "proposals_attempted",
    "accepted_moves",
    "noop_rejections",
    "geometry_rejections",
    "wire_reach_rejections",
    "metropolis_rejections",
    "implementation_proposals",
    "relay_proposals",
    "implementation_moves_accepted",
    "relay_moves_accepted",
    "swap_attempts",
    "swaps_accepted",
    "topology_rebuild_attempts",
    "topology_rebuild_successes",
)


@contextmanager
def _tracking(enabled: bool) -> Iterator[None]:
    original = incremental._TRACK_EXACT_ACCEPTED_MOVES
    incremental._TRACK_EXACT_ACCEPTED_MOVES = enabled
    try:
        yield
    finally:
        incremental._TRACK_EXACT_ACCEPTED_MOVES = original


def _run(factory: CaseFactory, *, proposals: int, seed: int, tracking: bool):
    case = factory()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=proposals,
        random_seed=seed,
        restarts=1,
    )
    started = perf_counter()
    with _tracking(tracking):
        observed = optimize_physical_layout_observed(case.problem, options=options)
    elapsed = perf_counter() - started
    validate_physical_layout(replace(case.problem, layout=observed.optimization.layout))
    return observed, elapsed


def _assert_same_trajectory(baseline: Any, candidate: Any) -> None:
    for field in _TRAJECTORY_FIELDS:
        left = getattr(baseline.stats, field)
        right = getattr(candidate.stats, field)
        if left != right:
            raise AssertionError(f"trajectory counter {field} changed: {left} != {right}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=0, help="first random seed")
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
    if args.proposals <= 0:
        parser.error("--proposals must be positive")
    if args.seeds <= 0:
        parser.error("--seeds must be positive")

    cases: tuple[tuple[str, CaseFactory], ...] = (
        ("relay-forest", _relay_forest_case),
        ("shared-bus", _shared_bus_case),
        ("clustered-sparse-cut", _clustered_sparse_cut_case),
        ("narrow-corridor", _narrow_corridor_case),
        ("perimeter-anchor", _perimeter_anchor_case),
        ("fixed-endpoint-span", _fixed_endpoint_span_case),
    )
    totals = {"better": 0, "equal": 0, "worse": 0}
    ratios: list[float] = []
    for name, factory in cases:
        case_ratios: list[float] = []
        case_counts = {"better": 0, "equal": 0, "worse": 0}
        for seed in range(args.seed, args.seed + args.seeds):
            baseline, baseline_time = _run(
                factory,
                proposals=args.proposals,
                seed=seed,
                tracking=False,
            )
            candidate, candidate_time = _run(
                factory,
                proposals=args.proposals,
                seed=seed,
                tracking=True,
            )
            _assert_same_trajectory(baseline, candidate)
            before = baseline.optimization.after.objective
            after = candidate.optimization.after.objective
            if after < before:
                outcome = "better"
            elif after > before:
                outcome = "worse"
            else:
                outcome = "equal"
            if outcome == "worse":
                raise AssertionError(
                    f"incremental exact tracking lost a baseline state for {name} seed {seed}: "
                    f"{before} -> {after}"
                )
            ratio = candidate_time / baseline_time if baseline_time else 1.0
            case_ratios.append(ratio)
            ratios.append(ratio)
            case_counts[outcome] += 1
            totals[outcome] += 1
            print(
                f"{name} seed={seed}: baseline={before}, tracked={after}, outcome={outcome}, "
                f"accepted={candidate.stats.accepted_moves}, "
                f"runtime={baseline_time:.3f}s->{candidate_time:.3f}s"
            )
        print(
            f"SUMMARY {name}: better/equal/worse={case_counts['better']}/"
            f"{case_counts['equal']}/{case_counts['worse']}, "
            f"median-runtime-ratio={median(case_ratios):.3f}"
        )
    print(
        f"OVERALL better/equal/worse={totals['better']}/{totals['equal']}/{totals['worse']}, "
        f"median-runtime-ratio={median(ratios):.3f}"
    )


if __name__ == "__main__":
    main()
''')

roadmap = "docs/roadmap.md"
replace_once(
    roadmap,
    '''- **Current: exact mid-epoch best tracking.** The annealer currently samples the true `(relay_count, occupied_area, wire_length)` objective only at epoch boundaries. Record any better exact state immediately after an accepted move without changing the proposal, RNG, or acceptance trajectory.
''',
    '''- **Promising: exact mid-epoch best tracking.** Full rescoring after every accepted move found transient lexicographic improvements without changing the search trajectory, but cost roughly 1.7x-2.3x on active cases. Sampling every 4 accepted moves preserved the observed gain in the short-stride matrix but still cost up to about 33%; stride 8 and above missed it. The current experiment keeps every-move sampling while maintaining footprint extrema and incident-wire length incrementally.
''',
)
replace_once(
    roadmap,
    '''Continue **Milestone C** with exact mid-epoch best tracking. This experiment deliberately leaves the visited annealing trajectory unchanged and only samples the public lexicographic objective after accepted moves, so a fixed seed cannot lose a state the baseline would have returned. Keep it only if the corpus shows useful objective gains at acceptable scoring overhead; if the idea is valuable but expensive, optimize the exact-score update incrementally rather than weakening the objective check.''',
    '''Continue **Milestone C** with incremental exact mid-epoch best tracking. Preserve every accepted-move sampling of the public lexicographic objective, but maintain footprint extrema with lazy heaps and wire length through incident-wire deltas so scoring stays local. Accept this only if paired corpus runs keep the same proposal/acceptance trajectory, never lose a baseline objective, retain useful transient improvements, and reduce the prior full-rescan overhead to a small fraction.''',
)

workflow = Path(".github/workflows/exact-best-comparison.yml")
workflow.write_text('''name: Exact best comparison

on:
  push:
    branches:
      - agent/annealing-v2-exact-best-tracking

jobs:
  compare:
    if: "${{ github.event.head_commit.message == 'ci: run incremental exact tracker comparison' }}"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Install uv
        run: python -m pip install uv
      - name: Install dependencies
        run: uv sync --extra dev
      - name: Compare epoch-only and local exact tracking
        run: uv run python -m benchmarks.layout_optimizer_exact_best_compare --proposals 4096 --seeds 3
''')
