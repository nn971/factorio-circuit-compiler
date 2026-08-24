"""Whole-vector physical synthesis extension."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
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
from factorio_circuit.synthesis.incremental_joint_layout import refine_incremental_joint_layout
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

    return 1 if options.strategy == "row" else options.restarts


def _placement_attempt_options(options: PlacementOptions, restart: int) -> PlacementOptions:
    """Build one deterministic layout attempt.

    Annealed retries now keep one physical envelope/corridor geometry and vary only the random seed.
    Empty placement sites are working space for implementation entities and relays, not a retry-time
    dropout ratio. Historical greedy routing retains its progressively looser retry geometry.
    """

    if options.strategy in {"annealed", "net-aware"}:
        return replace(
            options,
            random_seed=options.random_seed + restart,
            restarts=1,
        )

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


def _retryable_layout_error(error: ValueError) -> bool:
    message = str(error)
    return any(
        marker in message
        for marker in (
            "parallel lanes and grid search were both exhausted",
            "joint relay seed left physical net group",
            "outside conservative wire reach",
            "could not recover annealed candidate grid",
            "joint annealing could not repair the final placement",
        )
    )


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
        wire_length += (
            (source[0] - target[0]) ** 2 + (source[1] - target[1]) ** 2
        ) ** 0.5

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
        annealed = strategy in {"annealed", "net-aware"}

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

            # Annealed synthesis now has one hot loop: first build a deterministic legal seed,
            # then jointly anneal implementation entities and relays. Running the historical
            # exact-net annealer here would duplicate work and restore the O(k^2)-per-proposal
            # bottleneck that large Snake nets exposed.
            seed_options = replace(attempt_options, iterations=0) if annealed else attempt_options
            placement = plan_physical_circuit(
                physical,
                self.circuit,
                net_groups,
                safe_wire_span=self.safe_wire_span,
                options=seed_options,
            )
            positions = placement.positions
            report_progress(
                self.progress,
                "placement",
                completed=restart + 1,
                total=attempts,
                detail=f"seeded {len(positions)} entities",
            )

            try:
                if annealed:
                    report_progress(
                        self.progress,
                        "joint-layout",
                        detail=(
                            "incrementally annealing implementation entities and relays; "
                            "exact net trees rebuild at epoch boundaries"
                        ),
                    )
                    joint = refine_incremental_joint_layout(
                        physical,
                        self.circuit,
                        net_groups,
                        net_colors,
                        positions,
                        safe_wire_span=self.safe_wire_span,
                        options=attempt_options,
                    )
                    positions = joint.positions
                    routing = joint.routing
                    self._materialize_connections(physical, net_colors, net_groups, positions)
                    report_progress(
                        self.progress,
                        "routing",
                        completed=len(routing.wires),
                        total=len(routing.wires),
                        detail=f"shared-net routing complete; relays={len(routing.relays)}",
                    )
                else:
                    self._materialize_connections(physical, net_colors, net_groups, positions)
                    routing = route_wires(
                        physical,
                        positions,
                        safe_span=self.safe_wire_span,
                        relay_forbidden_areas=placement.relay_forbidden_areas,
                        progress=self.progress,
                    )
            except ValueError as exc:
                if not _retryable_layout_error(exc):
                    raise
                last_routing_error = exc
                if restart + 1 < attempts:
                    next_options = _placement_attempt_options(selected, restart + 1)
                    detail = (
                        f"joint layout failed; retrying with seed={next_options.random_seed}"
                        if annealed
                        else (
                            "routing failed; rebuilding with more space: "
                            f"fill={next_options.target_fill:.3f}; "
                            f"corridor={next_options.corridor_width:.2f}"
                        )
                    )
                    report_progress(
                        self.progress,
                        "retry",
                        completed=restart + 1,
                        total=attempts,
                        detail=detail,
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

            if score[0] == 0:
                break

        if best_candidate is None:
            assert last_routing_error is not None
            raise ValueError(
                f"physical synthesis exhausted {attempts} deterministic placement attempt(s) "
                "without finding a collision-free, reach-safe joint layout outside reserved "
                "corridors"
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
