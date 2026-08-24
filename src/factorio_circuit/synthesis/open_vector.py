"""Whole-vector physical synthesis extension."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import hypot
from typing import Any

from factorio_circuit.blueprint.routing import RoutingPlan, route_wires, routed_positions
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ConstantCombinator,
    DeciderCombinator,
    PhysicalCircuit,
    SelectorCombinator,
    SignalId,
    WireColor,
)
from factorio_circuit.lowering.vector_unary import VECTOR_EACH_PLACEHOLDER
from factorio_circuit.progress import ProgressCallback, report_progress
from factorio_circuit.synthesis.joint_layout import refine_joint_layout
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.physical import PhysicalSynthesizer
from factorio_circuit.synthesis.placement import PlacementOptions, Position, plan_physical_circuit
from factorio_circuit.synthesis.placement_constraints import resolve_placement_constraints
from factorio_circuit.synthesis.safe_crossbar import build_safe_crossbar_layout
from factorio_circuit.synthesis.safe_folded_crossbar import build_safe_folded_crossbar_layout
from factorio_circuit.synthesis.signal_coloring import allocate_abstract_signals_dsat

LayoutScore = tuple[int, float, float, int]


@dataclass(frozen=True, slots=True)
class _LayoutCandidate:
    positions: dict[int, Position]
    routing: RoutingPlan
    score: LayoutScore
    restart: int


def _placement_attempt_count(options: PlacementOptions) -> int:
    """Return deterministic synthesis attempts for the requested placement policy."""

    # Row placement is invariant under target-fill/corridor retry parameters. Greedy annealed
    # placement (iterations=0), however, changes when the candidate grid is made sparser, so it
    # should retain deterministic retries instead of being forced to a single attempt.
    return 1 if options.strategy == "row" else options.restarts


def _placement_attempt_options(options: PlacementOptions, restart: int) -> PlacementOptions:
    """Make later deterministic attempts progressively easier to route."""

    scale = options.retry_fill_scale**restart
    corridor_width = options.corridor_width
    if options.reserve_corridors:
        corridor_width = options.corridor_width / scale
    return replace(
        options,
        random_seed=options.random_seed + restart,
        target_fill=options.target_fill * scale,
        corridor_width=corridor_width,
        restarts=1,
    )


def _connections_need_relays(
    circuit: PhysicalCircuit,
    positions: dict[int, Position],
    *,
    safe_wire_span: float,
) -> bool:
    """Return whether the chosen physical-net trees contain any over-reach edge."""

    for connection in circuit.connections:
        left = positions[connection.source.entity]
        right = positions[connection.target.entity]
        if hypot(left[0] - right[0], left[1] - right[1]) > safe_wire_span + 1e-9:
            return True
    return False


def _layout_candidate_score(
    circuit: PhysicalCircuit,
    positions: dict[int, Position],
    routing: RoutingPlan,
    *,
    restart: int,
) -> LayoutScore:
    """Rank legal layouts by relay count, occupied area, then total routed wire length.

    Relay count is deliberately dominant because every layout-only constant combinator is a real
    blueprint entity. The restart index is a final deterministic tie-break. Once a candidate has
    zero relays, synthesis may stop: the primary objective has reached its absolute lower bound.
    """

    final_positions = routed_positions(circuit, positions, routing)
    entities = {entity.id: entity for entity in circuit.entities}
    relay_ids = {relay.entity_id for relay in routing.relays}

    left = float("inf")
    right = float("-inf")
    top = float("inf")
    bottom = float("-inf")
    for entity_id, (x, y) in final_positions.items():
        half_x = (
            0.5
            if entity_id in relay_ids or isinstance(entities[entity_id], ConstantCombinator)
            else 1.0
        )
        left = min(left, x - half_x)
        right = max(right, x + half_x)
        top = min(top, y - 0.5)
        bottom = max(bottom, y + 0.5)
    area = 0.0 if not final_positions else (right - left) * (bottom - top)

    wire_length = 0.0
    for wire in routing.wires:
        source = final_positions[wire.source_entity]
        target = final_positions[wire.target_entity]
        wire_length += hypot(source[0] - target[0], source[1] - target[1])

    return (len(routing.relays), area, wire_length, restart)


@dataclass(slots=True)
class VectorPhysicalSynthesizer(PhysicalSynthesizer):
    progress: ProgressCallback | None = None
    anchor_positions: Mapping[str, Position] | None = None

    def _allocate_signals(self, net_groups: dict[int, int]) -> dict[int, SignalId]:
        """Color abstract lanes with deterministic DSATUR.

        Shared delay buses create dense interference cliques, so the vector path uses a dynamic
        saturation ordering rather than the baseline synthesizer's historical static degree order.
        """

        return allocate_abstract_signals_dsat(
            self.circuit,
            net_groups,
            signal_pool=self.signal_pool,
            reserved=self._fixed_signal_ids(),
            alias_roots=self._signal_alias_roots(),
        )

    def synthesize(self) -> Layout:
        report_progress(self.progress, "synthesis", detail="validating abstract physical circuit")
        self.circuit.validate()

        report_progress(self.progress, "synthesis", detail="assigning red/green net colors")
        net_colors = self._assign_net_colors()
        net_groups = self._coalesce_shared_connector_nets(net_colors)

        report_progress(self.progress, "synthesis", detail="allocating concrete signals")
        signal_allocation = self._allocate_signals(net_groups)

        report_progress(self.progress, "synthesis", detail="materializing physical combinators")
        physical = self._materialize_circuit(signal_allocation, net_colors)

        selected = resolve_placement_constraints(
            self.circuit,
            self.placement_options or PlacementOptions(),
            self.anchor_positions,
        )
        strategy = str(selected.strategy)
        if strategy in {"safe-crossbar", "safe-folded-crossbar"}:
            if selected.anchors:
                raise ValueError(
                    f"{strategy} does not yet support fixed placement anchors; "
                    "use the annealed layout for anchored synthesis"
                )
            if strategy == "safe-crossbar":
                report_progress(
                    self.progress,
                    "safe-layout",
                    detail="using canonical linear bus/feeder geometry; routing search disabled",
                )
                return build_safe_crossbar_layout(
                    self.circuit,
                    physical,
                    net_colors=net_colors,
                    net_groups=net_groups,
                    signal_allocation=signal_allocation,
                    safe_wire_span=self.safe_wire_span,
                    progress=self.progress,
                )

            report_progress(
                self.progress,
                "safe-folded-layout",
                detail="using row-local folded bus geometry; routing search disabled",
            )
            return build_safe_folded_crossbar_layout(
                self.circuit,
                physical,
                net_colors=net_colors,
                net_groups=net_groups,
                signal_allocation=signal_allocation,
                safe_wire_span=self.safe_wire_span,
                progress=self.progress,
            )

        selected.validate()
        attempts = _placement_attempt_count(selected)

        last_routing_error: ValueError | None = None
        best_candidate: _LayoutCandidate | None = None
        for restart in range(attempts):
            attempt_options = _placement_attempt_options(selected, restart)
            report_progress(
                self.progress,
                "placement",
                completed=restart,
                total=attempts,
                detail=(
                    f"strategy={attempt_options.strategy}; "
                    f"iterations={attempt_options.iterations}; "
                    f"fill={attempt_options.target_fill:.3f}; "
                    f"corridor={attempt_options.corridor_width:.2f}"
                ),
            )
            placement = plan_physical_circuit(
                physical,
                self.circuit,
                net_groups,
                safe_wire_span=self.safe_wire_span,
                options=attempt_options,
            )
            positions = placement.positions
            report_progress(
                self.progress,
                "placement",
                completed=restart + 1,
                total=attempts,
                detail=f"placed {len(positions)} entities",
            )

            # Materialize the relay-minimizing tree first. Most compiled circuits are already
            # directly reach-safe after placement; keep those on the cheap historical routing
            # path and invoke the joint combinator/relay refinement only when a relay is actually
            # required.
            self._materialize_connections(physical, net_colors, net_groups, positions)
            needs_joint = strategy in {"annealed", "net-aware"} and _connections_need_relays(
                physical,
                positions,
                safe_wire_span=self.safe_wire_span,
            )

            try:
                if needs_joint:
                    report_progress(
                        self.progress,
                        "joint-layout",
                        detail="jointly refining relay-bearing physical nets",
                    )
                    joint = refine_joint_layout(
                        physical,
                        self.circuit,
                        net_groups,
                        net_colors,
                        positions,
                        safe_wire_span=self.safe_wire_span,
                        options=attempt_options,
                        relay_forbidden_areas=placement.relay_forbidden_areas,
                    )
                    positions = joint.positions
                    routing = joint.routing
                    # The concrete circuit keeps a conventional terminal spanning tree for
                    # simulation/inspection. Blueprint routing itself uses the shared relay tree.
                    self._materialize_connections(physical, net_colors, net_groups, positions)
                    report_progress(
                        self.progress,
                        "routing",
                        completed=len(routing.wires),
                        total=len(routing.wires),
                        detail=f"shared-net routing complete; relays={len(routing.relays)}",
                    )
                else:
                    routing = route_wires(
                        physical,
                        positions,
                        safe_span=self.safe_wire_span,
                        relay_forbidden_areas=placement.relay_forbidden_areas,
                        progress=self.progress,
                    )
            except ValueError as exc:
                if "parallel lanes and grid search were both exhausted" not in str(exc):
                    raise
                last_routing_error = exc
                if restart + 1 < attempts:
                    next_options = _placement_attempt_options(selected, restart + 1)
                    report_progress(
                        self.progress,
                        "retry",
                        completed=restart + 1,
                        total=attempts,
                        detail=(
                            "routing failed; rebuilding with more space: "
                            f"fill={next_options.target_fill:.3f}; "
                            f"corridor={next_options.corridor_width:.2f}"
                        ),
                    )
                continue

            score = _layout_candidate_score(physical, positions, routing, restart=restart)
            candidate = _LayoutCandidate(dict(positions), routing, score, restart)
            if best_candidate is None or candidate.score < best_candidate.score:
                best_candidate = candidate
            report_progress(
                self.progress,
                "selection",
                completed=restart + 1,
                total=attempts,
                detail=(
                    f"candidate={restart + 1}; relays={score[0]}; "
                    f"area={score[1]:.1f}; wire={score[2]:.1f}"
                ),
            )

            # Zero relays is the theoretical minimum for the dominant objective. Later retries
            # intentionally expand the placement basin, so do not spend extra synthesis work on
            # secondary tie-breaking after reaching that minimum.
            if score[0] == 0:
                break

        if best_candidate is None:
            assert last_routing_error is not None
            raise ValueError(
                f"physical synthesis exhausted {attempts} deterministic placement attempt(s) "
                "without finding a collision-free, reach-safe route that keeps reserved "
                "corridors clear of relay entities"
            ) from last_routing_error

        positions = best_candidate.positions
        routing = best_candidate.routing
        self._materialize_connections(physical, net_colors, net_groups, positions)
        final_positions = routed_positions(physical, positions, routing)
        report_progress(
            self.progress,
            "synthesis",
            detail=(
                f"physical layout complete; combinators={physical.combinator_count}; "
                f"relays={len(routing.relays)}; selected_attempt={best_candidate.restart + 1}"
            ),
        )
        return Layout(
            circuit=physical,
            positions=final_positions,
            relays=tuple(
                LayoutRelay(relay.entity_id, relay.position, relay.description)
                for relay in routing.relays
            ),
            wires=tuple(
                LayoutWire(
                    wire.source_entity,
                    wire.source_connector_id,
                    wire.target_entity,
                    wire.target_connector_id,
                    wire.color,
                )
                for wire in routing.wires
            ),
            signal_allocation=tuple(sorted(signal_allocation.items())),
            net_colors=tuple(sorted(net_colors.items())),
            net_groups=tuple(sorted(net_groups.items())),
        )

    def _materialize_entity(
        self,
        entity: abstract.AbstractEntity,
        signals: dict[int, SignalId],
        net_colors: dict[int, WireColor],
        annotation_descriptions: dict[int, str],
    ) -> Any:
        if isinstance(entity, abstract.SelectorCombinator):
            return SelectorCombinator(
                id=entity.id,
                operation=entity.operation,
                select_max=entity.select_max,
                index=entity.index,
                random_update_interval=entity.random_update_interval,
                description=entity.description,
            )

        result = super(VectorPhysicalSynthesizer, self)._materialize_entity(
            entity,
            signals,
            net_colors,
            annotation_descriptions,
        )
        if isinstance(entity, abstract.DeciderCombinator) and isinstance(entity.output_signal, int):
            output = self.circuit.signal_by_id(entity.output_signal)
            if output.label == VECTOR_EACH_PLACEHOLDER:
                assert isinstance(result, DeciderCombinator)
                return replace(result, output_signal=SignalId("virtual", "signal-each"))
        return result


def synthesize_vector_layout(
    circuit: abstract.AbstractPhysicalCircuit,
    *,
    safe_wire_span: float,
    placement: PlacementOptions | None = None,
    anchor_positions: Mapping[str, Position] | None = None,
    progress: ProgressCallback | None = None,
) -> Layout:
    return VectorPhysicalSynthesizer(
        circuit,
        safe_wire_span=safe_wire_span,
        placement_options=placement,
        progress=progress,
        anchor_positions=anchor_positions,
    ).synthesize()
