from __future__ import annotations

import pytest

from factorio_circuit.blueprint.routing import BlueprintRelay, RoutedWire, RoutingPlan
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import ConstantCombinator, PhysicalCircuit, WireColor
from factorio_circuit.synthesis import incremental_joint_layout as incremental
from factorio_circuit.synthesis import joint_layout as exact
from factorio_circuit.synthesis.placement import PlacementOptions


def _relay_state() -> tuple[exact._JointState, incremental._FeasibleTopology]:
    circuit = PhysicalCircuit(
        "adaptive_retopology",
        entities=[ConstantCombinator(entity_id) for entity_id in (1, 2)],
    )
    state = exact._JointState(
        circuit=circuit,
        endpoints_by_group={
            1: (
                abstract.Endpoint(1, abstract.Connector.SINGLE),
                abstract.Endpoint(2, abstract.Connector.SINGLE),
            )
        },
        colors_by_group={1: WireColor.RED},
        positions={1: (0.0, 0.0), 2: (3.0, 0.0)},
        relay_positions={3: (1.5, 0.0)},
        relay_groups={3: frozenset({1})},
        safe_span=2.0,
        forbidden_areas=(),
    )
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
    return state, topology


def _reason(
    *,
    epoch_end: int = 256,
    iterations: int = 2048,
    scheduled_rebuilds: set[int] | None = None,
    stagnation: int = 0,
    cooldown: int | None = None,
    proposals: int = 256,
    reach_rejections: int = 0,
) -> str | None:
    if cooldown is None:
        cooldown = incremental._ADAPTIVE_REBUILD_COOLDOWN_EPOCHS
    return incremental._anneal_rebuild_reason(
        epoch_end=epoch_end,
        iterations=iterations,
        scheduled_rebuilds=scheduled_rebuilds or set(),
        epochs_since_improvement=stagnation,
        epochs_since_rebuild=cooldown,
        epoch_proposals=proposals,
        epoch_wire_reach_rejections=reach_rejections,
    )


def test_scheduled_rebuild_has_priority_over_adaptive_cooldown() -> None:
    assert _reason(scheduled_rebuilds={256}, cooldown=0) == "scheduled"


def test_adaptive_rebuild_triggers_at_wire_reach_pressure_threshold() -> None:
    assert _reason(reach_rejections=64) == "wire-reach-pressure"
    assert _reason(reach_rejections=63) is None


def test_adaptive_rebuild_triggers_after_sustained_stagnation() -> None:
    threshold = incremental._ADAPTIVE_REBUILD_STAGNATION_EPOCHS
    assert _reason(stagnation=threshold - 1) is None
    assert _reason(stagnation=threshold) == "stagnation"


def test_adaptive_rebuild_respects_cooldown_and_skips_final_epoch() -> None:
    assert _reason(reach_rejections=256, cooldown=1) is None
    assert _reason(epoch_end=2048, iterations=2048, reach_rejections=256) is None


def test_stagnation_adds_rebuilds_beyond_fixed_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    state, topology = _relay_state()
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        target_fill=0.6,
        iterations=4096,
        random_seed=0,
        restarts=1,
    )
    grid = incremental.base_placement._candidate_grid(4, 1, options)
    rebuild_calls: list[int] = []

    def stay_put(
        _state: exact._JointState,
        _object_id: int,
        _grid: incremental.base_placement._GridGeometry,
        _preferred: tuple[float, float],
        current: tuple[float, float],
        _rng: object,
        _normalized_temperature: float,
    ) -> tuple[float, float]:
        return current

    def record_rebuild(
        _state: exact._JointState,
        current: incremental._FeasibleTopology,
        _grid: incremental.base_placement._GridGeometry,
        *,
        diagnostics: list[str] | None = None,
    ) -> incremental._FeasibleTopology:
        _ = diagnostics
        rebuild_calls.append(1)
        return current

    monkeypatch.setattr(incremental, "_proposed_position", stay_put)
    monkeypatch.setattr(incremental, "_try_rebuild_annealed_topology", record_rebuild)

    incremental._anneal_feasible(state, topology, options, grid)

    assert len(rebuild_calls) > len(incremental._ANNEAL_REBUILD_FRACTIONS)


def test_coarse_retopology_never_replaces_a_fixed_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    state, topology = _relay_state()
    state.fixed_objects = frozenset({3})
    options = PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        target_fill=0.6,
        iterations=0,
    )
    grid = incremental.base_placement._candidate_grid(4, 1, options)

    def unexpected_bootstrap(*_args: object, **_kwargs: object) -> incremental._FeasibleTopology:
        raise AssertionError("fixed relay must prevent destructive coarse retopology")

    monkeypatch.setattr(incremental, "_construct_feasible_bootstrap", unexpected_bootstrap)

    result = incremental._try_rebuild_annealed_topology(state, topology, grid)

    assert result is topology
    assert state.relay_positions == {3: (1.5, 0.0)}
    assert state.relay_groups == {3: frozenset({1})}
