"""Whole-vector physical synthesis extension."""

from dataclasses import dataclass, replace
from typing import Any

from factorio_circuit.blueprint.routing import route_wires, routed_positions
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import DeciderCombinator, SignalId, WireColor
from factorio_circuit.lowering.vector_unary import VECTOR_EACH_PLACEHOLDER
from factorio_circuit.progress import ProgressCallback, report_progress
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.physical import PhysicalSynthesizer
from factorio_circuit.synthesis.placement import PlacementOptions, plan_physical_circuit


def _placement_attempt_count(options: PlacementOptions) -> int:
    """Return deterministic synthesis attempts for the requested placement policy."""

    # Row placement is invariant under target-fill/corridor retry parameters.  Greedy net-aware
    # placement (iterations=0), however, *does* change when the candidate grid is made sparser, so it
    # should retain deterministic retries instead of being forced to a single attempt.
    return 1 if options.strategy == "row" else options.restarts


def _placement_attempt_options(options: PlacementOptions, restart: int) -> PlacementOptions:
    """Make later deterministic attempts progressively easier to route.

    Lower target fill supplies more unused grid slots.  When routing corridors are enabled, widen
    them by the inverse factor as well.  This deliberately spends area after a routing failure rather
    than repeating nearly the same hostile geometry.
    """

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


@dataclass(slots=True)
class VectorPhysicalSynthesizer(PhysicalSynthesizer):
    progress: ProgressCallback | None = None

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

        selected = self.placement_options or PlacementOptions()
        selected.validate()
        attempts = _placement_attempt_count(selected)

        last_routing_error: ValueError | None = None
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

            self._materialize_connections(physical, net_colors, net_groups, positions)
            try:
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

            final_positions = routed_positions(physical, positions, routing)
            report_progress(
                self.progress,
                "synthesis",
                detail=(
                    f"physical layout complete; combinators={physical.combinator_count}; "
                    f"relays={len(routing.relays)}"
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

        assert last_routing_error is not None
        raise ValueError(
            f"physical synthesis exhausted {attempts} deterministic placement attempt(s) "
            "without finding a collision-free, reach-safe route that keeps reserved "
            "corridors clear of relay entities"
        ) from last_routing_error

    def _materialize_entity(
        self,
        entity: abstract.AbstractEntity,
        signals: dict[int, SignalId],
        net_colors: dict[int, WireColor],
        annotation_descriptions: dict[int, str],
    ) -> Any:
        result = super()._materialize_entity(
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
    progress: ProgressCallback | None = None,
) -> Layout:
    return VectorPhysicalSynthesizer(
        circuit,
        safe_wire_span=safe_wire_span,
        placement_options=placement,
        progress=progress,
    ).synthesize()
