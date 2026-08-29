"""Compose typed oracle-provider rigid components before final physical routing.

E2 uses short-lived connector proxies only as inputs to the existing placement machinery. A proxy
sits at a conservative connector-side position inside the already-declared rigid footprint and
carries the abstract net that will eventually attach to the real device port. After placement, the
ordinary implementation is legalized against the complete D1 component geometry, routing is rebuilt
with those regions reserved from relays, and every proxy endpoint is replaced by the exact opaque
device endpoint before the final layout is validated.

The serialized artifact therefore never contains proxies and never appends a device after routing.
The reusable component, its electrical constraints, and its geometry all participate before the
final route is constructed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from math import hypot

from factorio_circuit.blueprint import routing as wire_routing
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    Connector,
    OpaqueDualConnectorEntity,
    OpaqueSingleConnectorEntity,
    PhysicalCircuit,
    PhysicalEntity,
    SignalId,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.progress import ProgressCallback, report_progress
from factorio_circuit.provider_products import ProviderRigidComponentProduct
from factorio_circuit.synthesis import placement as base_placement
from factorio_circuit.synthesis.blueprint_component import (
    ImportedBlueprintLayout,
    import_blueprint_layout,
)
from factorio_circuit.synthesis.component_geometry import (
    ComponentLayoutOptimizationProblem,
    ComponentRegion,
    RigidComponentConstraint,
    validate_component_layout_problem,
)
from factorio_circuit.synthesis.imported_component_geometry import (
    imported_layout_as_rigid_component,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.layout_optimizer import (
    LayoutOptimizationProblem,
    LegalPlacementLattice,
)
from factorio_circuit.synthesis.open_vector import VectorPhysicalSynthesizer
from factorio_circuit.synthesis.placement import PlacementOptions, Position
from factorio_circuit.synthesis.placement_constraints import resolve_placement_constraints
from factorio_circuit.synthesis.signal_coloring import allocate_abstract_signals_dsat

_EPSILON = 1e-9
_MAX_LEGALIZATION_RADIUS = 128


@dataclass(frozen=True, slots=True)
class _PortProxy:
    proxy_id: int
    actual_entity_id: int
    actual_connector: Connector
    actual_connector_id: int
    net_id: int
    color: WireColor
    position: Position
    actual_position: Position

    @property
    def displacement(self) -> float:
        return hypot(
            self.position[0] - self.actual_position[0],
            self.position[1] - self.actual_position[1],
        )


@dataclass(frozen=True, slots=True)
class _RebasedComponent:
    product: ProviderRigidComponentProduct
    entities: tuple[PhysicalEntity, ...]
    positions: Mapping[int, Position]
    connections: tuple[WireConnection, ...]
    wires: tuple[LayoutWire, ...]
    constraint: RigidComponentConstraint
    source_to_actual: Mapping[int, int]


@dataclass(frozen=True, slots=True)
class _CompositionPlan:
    abstract_circuit: abstract.AbstractPhysicalCircuit
    placement: PlacementOptions
    components: tuple[_RebasedComponent, ...]
    proxies: tuple[_PortProxy, ...]
    required_net_colors: Mapping[int, WireColor]
    required_signal_allocations: Mapping[int, SignalId]
    forbidden_regions: tuple[ComponentRegion, ...]

    @property
    def proxy_ids(self) -> frozenset[int]:
        return frozenset(proxy.proxy_id for proxy in self.proxies)

    @property
    def validation_wire_span(self) -> float:
        return max(component.product.internal_wire_span for component in self.components)


@dataclass(slots=True)
class _ProviderVectorSynthesizer(VectorPhysicalSynthesizer):
    """Existing vector synthesis plus provider-required color/signal assignments."""

    required_net_colors: Mapping[int, WireColor] = field(default_factory=dict)
    required_signal_allocations: Mapping[int, SignalId] = field(default_factory=dict)

    def _color_net_constraints(
        self,
        hard_conflicts: set[tuple[int, int]],
        preferences: dict[tuple[int, int], int],
    ) -> dict[int, WireColor]:
        colors = VectorPhysicalSynthesizer._color_net_constraints(
            self,
            hard_conflicts,
            preferences,
        )
        if not self.required_net_colors:
            return colors

        adjacency: dict[int, set[int]] = {net.id: set() for net in self.circuit.nets}
        for left, right in hard_conflicts:
            adjacency[left].add(right)
            adjacency[right].add(left)

        component_of: dict[int, int] = {}
        next_component_id = 0
        for start in sorted(adjacency):
            if start in component_of:
                continue
            component_of[start] = next_component_id
            queue = deque([start])
            while queue:
                current = queue.popleft()
                for neighbor in sorted(adjacency[current]):
                    if neighbor not in component_of:
                        component_of[neighbor] = next_component_id
                        queue.append(neighbor)
            next_component_id += 1

        required_flip: dict[int, bool] = {}
        for net_id, desired in self.required_net_colors.items():
            if net_id not in colors:
                raise ValueError(f"provider component binds unknown abstract net {net_id}")
            component = component_of[net_id]
            flip = colors[net_id] is not desired
            previous = required_flip.setdefault(component, flip)
            if previous != flip:
                raise ValueError(
                    "provider component wire-color requirements conflict through abstract "
                    f"net constraints at net {net_id}"
                )

        for net_id, component in component_of.items():
            if not required_flip.get(component, False):
                continue
            colors[net_id] = WireColor.GREEN if colors[net_id] is WireColor.RED else WireColor.RED
        return colors

    def _allocate_signals(self, net_groups: dict[int, int]) -> dict[int, SignalId]:
        return allocate_abstract_signals_dsat(
            self.circuit,
            net_groups,
            signal_pool=self.signal_pool,
            reserved=self._fixed_signal_ids(),
            alias_roots=self._signal_alias_roots(),
            fixed_allocations=self.required_signal_allocations,
        )


def synthesize_provider_component_layout(
    circuit: abstract.AbstractPhysicalCircuit,
    products: tuple[ProviderRigidComponentProduct, ...],
    *,
    safe_wire_span: float,
    placement: PlacementOptions | None = None,
    anchor_positions: Mapping[str, Position] | None = None,
    progress: ProgressCallback | None = None,
) -> Layout:
    """Jointly place compiler logic around rigid provider products and build one final route."""

    if not products:
        raise ValueError("provider component synthesis requires at least one rigid product")
    if safe_wire_span <= 2.0:
        raise ValueError("provider component synthesis requires wire span greater than two tiles")

    plan = _prepare_composition(circuit, products, placement or PlacementOptions())
    report_progress(
        progress,
        "provider-composition",
        detail=(
            f"placing logic with {len(plan.components)} rigid provider component(s) and "
            f"{len(plan.proxies)} typed port proxy/proxies"
        ),
    )
    synthesizer = _ProviderVectorSynthesizer(
        plan.abstract_circuit,
        safe_wire_span=safe_wire_span,
        placement_options=plan.placement,
        progress=progress,
        anchor_positions=anchor_positions,
        required_net_colors=plan.required_net_colors,
        required_signal_allocations=plan.required_signal_allocations,
    )
    proxy_layout = synthesizer.synthesize()

    resolved = resolve_placement_constraints(
        plan.abstract_circuit,
        plan.placement,
        anchor_positions,
    )
    fixed_ids = set(resolved.anchors)
    if resolved.anchor_io:
        fixed_ids.update(port.marker_entity for port in proxy_layout.circuit.inputs)
        fixed_ids.update(port.marker_entity for port in proxy_layout.circuit.outputs)

    implementation_ids = {entity.id for entity in proxy_layout.circuit.entities}
    proxy_positions = {
        entity_id: proxy_layout.positions[entity_id] for entity_id in implementation_ids
    }
    _legalize_ordinary_implementation(
        proxy_layout.circuit,
        proxy_positions,
        forbidden_regions=plan.forbidden_regions,
        proxy_ids=plan.proxy_ids,
        fixed_ids=fixed_ids,
    )

    net_colors = proxy_layout.assigned_net_colors
    net_groups = proxy_layout.coalesced_net_groups
    synthesizer._materialize_connections(
        proxy_layout.circuit,
        net_colors,
        net_groups,
        proxy_positions,
    )

    max_proxy_shift = max((proxy.displacement for proxy in plan.proxies), default=0.0)
    constructive_span = safe_wire_span - 2.0 * max_proxy_shift
    if constructive_span <= 2.0:
        raise ValueError(
            "provider component connector proxy margin leaves too little reach for safe routing"
        )
    forbidden_areas = tuple(_region_as_area(region) for region in plan.forbidden_regions)
    report_progress(
        progress,
        "provider-composition",
        detail=(
            "fresh-routing combined logic before exact device endpoint substitution; "
            f"constructive_span={constructive_span:.3f}"
        ),
    )
    routing = wire_routing.route_wires(
        proxy_layout.circuit,
        proxy_positions,
        safe_span=constructive_span,
        relay_forbidden_areas=forbidden_areas,
        progress=progress,
    )

    final_layout = _materialize_final_layout(
        proxy_layout,
        proxy_positions,
        routing,
        plan,
    )
    validation_span = max(safe_wire_span, plan.validation_wire_span)
    _validate_final_components(final_layout, plan, validation_span)
    wire_routing.validate_wire_spans(
        tuple(
            wire_routing.RoutedWire(
                wire.source_entity,
                wire.source_connector_id,
                wire.target_entity,
                wire.target_connector_id,
                wire.color,
            )
            for wire in final_layout.wires
            if not _is_internal_wire(wire, plan)
        ),
        final_layout.positions,
        maximum_span=safe_wire_span,
    )
    report_progress(
        progress,
        "provider-composition",
        detail=(
            f"validated mixed layout; opaque_entities="
            f"{sum(len(component.entities) for component in plan.components)}; "
            f"relays={len(final_layout.relays)}"
        ),
    )
    return final_layout


def _prepare_composition(
    circuit: abstract.AbstractPhysicalCircuit,
    products: tuple[ProviderRigidComponentProduct, ...],
    placement: PlacementOptions,
) -> _CompositionPlan:
    copied = deepcopy(circuit)
    existing_names: set[str] = set()
    next_id = max((entity.id for entity in copied.entities), default=0) + 1
    pending: list[
        tuple[ProviderRigidComponentProduct, ImportedBlueprintLayout, dict[int, int]]
    ] = []

    for product in products:
        if product.name in existing_names:
            raise ValueError(f"duplicate provider rigid component name {product.name!r}")
        existing_names.add(product.name)
        imported = import_blueprint_layout(
            product.device.blueprint,
            prototype_specs=product.prototype_specs,
            name=product.name,
        )
        mapping: dict[int, int] = {}
        for source_id in sorted(imported.layout.positions):
            mapping[source_id] = next_id
            next_id += 1
        pending.append((product, imported, mapping))

    components: list[_RebasedComponent] = []
    actual_by_product: dict[str, _RebasedComponent] = {}
    for product, imported, mapping in pending:
        rebased = _rebase_component(product, imported, mapping)
        components.append(rebased)
        actual_by_product[product.name] = rebased

    proxy_anchors: dict[int, Position] = {}
    proxies: list[_PortProxy] = []
    required_colors: dict[int, WireColor] = {}
    required_signals: dict[int, SignalId] = {}
    net_ids = {net.id for net in copied.nets}

    for product in products:
        rebased = actual_by_product[product.name]
        for binding in product.port_bindings:
            if binding.net_id not in net_ids:
                raise ValueError(
                    f"provider component {product.name!r} binds unknown net {binding.net_id}"
                )
            port = product.device.port(binding.port_name)
            source_entity_id = port.endpoint.entity_number
            actual_entity_id = rebased.source_to_actual[source_entity_id]
            actual_entity = next(
                entity for entity in rebased.entities if entity.id == actual_entity_id
            )
            actual_position = rebased.positions[actual_entity_id]
            if actual_position != port.endpoint.position:
                raise ValueError(
                    f"provider component {product.name!r} port {binding.port_name!r} has stale "
                    "endpoint position metadata"
                )
            actual_connector = _decode_actual_connector(
                actual_entity,
                port.endpoint.connector_id,
                port.endpoint.wire,
            )
            proxy_position = _proxy_position(actual_entity, actual_connector, actual_position)
            proxy_id = next_id
            next_id += 1
            copied.entities.append(
                abstract.ConstantCombinator(
                    proxy_id,
                    description=f"[provider-port-proxy] {product.name}/{binding.port_name}",
                    annotation_only=True,
                )
            )
            _attach_proxy_to_net(copied, binding.net_id, proxy_id)
            proxy_anchors[proxy_id] = proxy_position
            proxies.append(
                _PortProxy(
                    proxy_id,
                    actual_entity_id,
                    actual_connector,
                    port.endpoint.connector_id,
                    binding.net_id,
                    port.endpoint.wire,
                    proxy_position,
                    actual_position,
                )
            )

            previous_color = required_colors.setdefault(binding.net_id, port.endpoint.wire)
            if previous_color is not port.endpoint.wire:
                raise ValueError(
                    f"provider net {binding.net_id} is required to be both red and green"
                )
            if port.spec.signal is not None:
                _record_required_signal(
                    copied,
                    binding.net_id,
                    port.spec.signal,
                    required_signals,
                )

    merged_anchors = dict(placement.anchors)
    collisions = sorted(set(merged_anchors) & set(proxy_anchors))
    if collisions:
        raise ValueError(f"provider proxy ids collide with placement anchors: {collisions}")
    merged_anchors.update(proxy_anchors)
    selected = replace(placement, anchors=merged_anchors)

    regions: list[ComponentRegion] = []
    for component in components:
        regions.extend(component.constraint.absolute_footprints())
        regions.extend(component.constraint.absolute_keepouts())
        regions.extend(component.constraint.absolute_adapter_regions())

    return _CompositionPlan(
        copied,
        selected,
        tuple(components),
        tuple(proxies),
        dict(required_colors),
        dict(required_signals),
        tuple(regions),
    )


def _rebase_component(
    product: ProviderRigidComponentProduct,
    imported: ImportedBlueprintLayout,
    mapping: Mapping[int, int],
) -> _RebasedComponent:
    entities: list[PhysicalEntity] = []
    for source in imported.layout.circuit.entities:
        actual_id = mapping[source.id]
        if isinstance(source, OpaqueSingleConnectorEntity):
            entities.append(
                OpaqueSingleConnectorEntity(
                    actual_id,
                    source.prototype,
                    deepcopy(source.blueprint_fields),
                    physical_half_extent=source.physical_half_extent,
                )
            )
        elif isinstance(source, OpaqueDualConnectorEntity):
            entities.append(
                OpaqueDualConnectorEntity(
                    actual_id,
                    source.prototype,
                    deepcopy(source.blueprint_fields),
                    physical_half_extent=source.physical_half_extent,
                )
            )
        else:  # pragma: no cover - importer only emits opaque entity shells
            raise AssertionError(f"unexpected imported entity {type(source).__name__}")

    positions = {
        mapping[source_id]: position for source_id, position in imported.layout.positions.items()
    }
    connections = tuple(
        WireConnection(
            WireEndpoint(mapping[connection.source.entity], connection.source.connector),
            WireEndpoint(mapping[connection.target.entity], connection.target.connector),
            connection.color,
        )
        for connection in imported.layout.circuit.connections
    )
    wires = tuple(
        LayoutWire(
            mapping[wire.source_entity],
            wire.source_connector_id,
            mapping[wire.target_entity],
            wire.target_connector_id,
            wire.color,
        )
        for wire in imported.layout.wires
    )
    circuit = PhysicalCircuit(
        product.name,
        entities=list(entities),
        connections=list(connections),
    )
    layout = Layout(circuit, dict(positions), (), wires, (), ())
    half_extents = tuple(
        sorted((mapping[source_id], half) for source_id, half in imported.half_extents)
    )
    rebased_import = ImportedBlueprintLayout(layout, half_extents)
    constraint = imported_layout_as_rigid_component(
        rebased_import,
        product.name,
        origin=product.origin,
        footprints=product.footprints,
        keepouts=product.keepouts,
        adapter_regions=product.adapter_regions,
        access_points=product.access_points,
        allowed_origins=product.allowed_origins,
    )
    return _RebasedComponent(
        product,
        tuple(entities),
        dict(positions),
        connections,
        wires,
        constraint,
        dict(mapping),
    )


def _decode_actual_connector(
    entity: PhysicalEntity,
    connector_id: int,
    color: WireColor,
) -> Connector:
    if connector_id <= 0:
        raise ValueError("provider device port connector id must be positive")
    expected_color = WireColor.RED if connector_id % 2 else WireColor.GREEN
    if expected_color is not color:
        raise ValueError("provider device port wire color disagrees with connector id")
    red_id = connector_id if color is WireColor.RED else connector_id - 1
    if isinstance(entity, OpaqueSingleConnectorEntity):
        if red_id != 1:
            raise ValueError("single-connector provider entity requires connector id 1/2")
        return Connector.SINGLE
    if isinstance(entity, OpaqueDualConnectorEntity):
        if red_id == 1:
            return Connector.INPUT
        if red_id == 3:
            return Connector.OUTPUT
        raise ValueError("dual-connector provider entity requires connector id 1/2/3/4")
    raise TypeError(entity)


def _proxy_position(
    entity: PhysicalEntity,
    connector: Connector,
    position: Position,
) -> Position:
    if connector is Connector.SINGLE:
        return position
    if not isinstance(entity, OpaqueDualConnectorEntity):
        raise TypeError(entity)
    shift = min(0.5, max(0.0, entity.physical_half_extent[0] - 0.5))
    if connector is Connector.INPUT:
        return (position[0] - shift, position[1])
    if connector is Connector.OUTPUT:
        return (position[0] + shift, position[1])
    raise AssertionError("unexpected dual connector")


def _attach_proxy_to_net(
    circuit: abstract.AbstractPhysicalCircuit,
    net_id: int,
    proxy_id: int,
) -> None:
    for index, net in enumerate(circuit.nets):
        if net.id != net_id:
            continue
        endpoint = abstract.Endpoint(proxy_id, abstract.Connector.SINGLE)
        circuit.nets[index] = replace(net, endpoints=(*net.endpoints, endpoint))
        return
    raise ValueError(f"provider component binds missing abstract net {net_id}")


def _record_required_signal(
    circuit: abstract.AbstractPhysicalCircuit,
    net_id: int,
    concrete: SignalId,
    required: dict[int, SignalId],
) -> None:
    net = circuit.net_by_id(net_id)
    if len(net.signals) == 1:
        signal_id = net.signals[0]
        previous = required.setdefault(signal_id, concrete)
        if previous != concrete:
            raise ValueError(
                f"abstract signal {signal_id} receives conflicting provider signal identities"
            )
        return
    if not net.signals and concrete in net.fixed_signals:
        return
    if not net.signals:
        raise ValueError(
            f"scalar provider port on net {net_id} has no abstract or matching fixed signal lane"
        )
    raise ValueError(
        f"scalar provider port on net {net_id} ambiguously carries {len(net.signals)} lanes"
    )


def _legalize_ordinary_implementation(
    circuit: PhysicalCircuit,
    positions: dict[int, Position],
    *,
    forbidden_regions: tuple[ComponentRegion, ...],
    proxy_ids: frozenset[int],
    fixed_ids: set[int],
) -> None:
    if not forbidden_regions:
        return
    entities = {entity.id: entity for entity in circuit.entities}

    def overlaps_reserved(entity_id: int, position: Position) -> bool:
        half = base_placement._entity_half_extent(entities[entity_id])
        return any(region.overlaps_box(position, half) for region in forbidden_regions)

    def collision_free(entity_id: int, candidate: Position) -> bool:
        if overlaps_reserved(entity_id, candidate):
            return False
        half = base_placement._entity_half_extent(entities[entity_id])
        for other_id, other_position in positions.items():
            if other_id == entity_id:
                continue
            if base_placement._boxes_overlap(
                candidate,
                half,
                other_position,
                base_placement._entity_half_extent(entities[other_id]),
            ):
                return False
        return True

    for entity_id in sorted(entities):
        if entity_id in proxy_ids or not overlaps_reserved(entity_id, positions[entity_id]):
            continue
        if entity_id in fixed_ids:
            raise ValueError(
                f"fixed physical entity {entity_id} overlaps provider rigid component geometry"
            )
        origin = positions[entity_id]
        selected: Position | None = None
        for radius in range(1, _MAX_LEGALIZATION_RADIUS + 1):
            deltas = [
                (dx, dy)
                for dx in range(-radius, radius + 1)
                for dy in range(-radius, radius + 1)
                if max(abs(dx), abs(dy)) == radius
            ]
            deltas.sort(key=lambda item: (item[0] ** 2 + item[1] ** 2, item[0], item[1]))
            for dx, dy in deltas:
                candidate = (origin[0] + dx, origin[1] + dy)
                if collision_free(entity_id, candidate):
                    selected = candidate
                    break
            if selected is not None:
                break
        if selected is None:
            raise ValueError(
                f"could not legalize physical entity {entity_id} outside "
                "provider component geometry"
            )
        positions[entity_id] = selected

    base_placement._validate_entity_positions(circuit, positions)


def _materialize_final_layout(
    proxy_layout: Layout,
    proxy_positions: Mapping[int, Position],
    routing: wire_routing.RoutingPlan,
    plan: _CompositionPlan,
) -> Layout:
    proxy_by_id = {proxy.proxy_id: proxy for proxy in plan.proxies}
    ordinary_entities = [
        entity for entity in proxy_layout.circuit.entities if entity.id not in proxy_by_id
    ]
    component_entities = [entity for component in plan.components for entity in component.entities]
    final_circuit = PhysicalCircuit(
        proxy_layout.circuit.name,
        entities=[*ordinary_entities, *component_entities],
        connections=[],
        inputs=list(proxy_layout.circuit.inputs),
        outputs=list(proxy_layout.circuit.outputs),
    )

    final_connections: list[WireConnection] = []
    for connection in proxy_layout.circuit.connections:
        final_connections.append(
            WireConnection(
                _replace_proxy_endpoint(connection.source, proxy_by_id),
                _replace_proxy_endpoint(connection.target, proxy_by_id),
                connection.color,
            )
        )
    for component in plan.components:
        final_connections.extend(component.connections)
    final_circuit.connections = _deduplicate_connections(final_connections)

    relays = tuple(
        LayoutRelay(relay.entity_id, relay.position, relay.description) for relay in routing.relays
    )
    wires: list[LayoutWire] = []
    for wire in routing.wires:
        source_entity, source_connector = _replace_proxy_wire_side(
            wire.source_entity,
            wire.source_connector_id,
            proxy_by_id,
        )
        target_entity, target_connector = _replace_proxy_wire_side(
            wire.target_entity,
            wire.target_connector_id,
            proxy_by_id,
        )
        wires.append(
            LayoutWire(
                source_entity,
                source_connector,
                target_entity,
                target_connector,
                wire.color,
            )
        )
    for component in plan.components:
        wires.extend(component.wires)

    final_positions = {
        entity_id: position
        for entity_id, position in proxy_positions.items()
        if entity_id not in proxy_by_id
    }
    for component in plan.components:
        final_positions.update(component.positions)
    final_positions.update({relay.entity_id: relay.position for relay in routing.relays})

    return Layout(
        final_circuit,
        final_positions,
        relays,
        _deduplicate_wires(wires),
        proxy_layout.signal_allocation,
        proxy_layout.net_colors,
        proxy_layout.net_groups,
    )


def _replace_proxy_endpoint(
    endpoint: WireEndpoint,
    proxy_by_id: Mapping[int, _PortProxy],
) -> WireEndpoint:
    proxy = proxy_by_id.get(endpoint.entity)
    if proxy is None:
        return endpoint
    return WireEndpoint(proxy.actual_entity_id, proxy.actual_connector)


def _replace_proxy_wire_side(
    entity_id: int,
    connector_id: int,
    proxy_by_id: Mapping[int, _PortProxy],
) -> tuple[int, int]:
    proxy = proxy_by_id.get(entity_id)
    if proxy is None:
        return (entity_id, connector_id)
    return (proxy.actual_entity_id, proxy.actual_connector_id)


def _deduplicate_connections(connections: list[WireConnection]) -> list[WireConnection]:
    result: list[WireConnection] = []
    seen: set[tuple[tuple[int, str], tuple[int, str], WireColor]] = set()
    for connection in connections:
        left = (connection.source.entity, connection.source.connector.value)
        right = (connection.target.entity, connection.target.connector.value)
        if right < left:
            left, right = right, left
        key = (left, right, connection.color)
        if key in seen:
            continue
        seen.add(key)
        result.append(connection)
    return result


def _deduplicate_wires(wires: list[LayoutWire]) -> tuple[LayoutWire, ...]:
    result: list[LayoutWire] = []
    seen: set[tuple[int, int, int, int]] = set()
    for wire in wires:
        key = wire.as_factorio_tuple()
        if key in seen:
            continue
        seen.add(key)
        result.append(wire)
    return tuple(result)


def _validate_final_components(
    layout: Layout,
    plan: _CompositionPlan,
    safe_wire_span: float,
) -> None:
    component_ids = frozenset(
        member_id for component in plan.components for member_id in component.constraint.member_ids
    )
    ordinary_positions = {
        object_id: position
        for object_id, position in layout.positions.items()
        if object_id not in component_ids
    }
    sites = tuple(sorted(ordinary_positions.values()))
    lattice = LegalPlacementLattice(unit_sites=sites, wide_sites=sites)
    base = LayoutOptimizationProblem(
        layout,
        lattice,
        safe_wire_span=safe_wire_span,
    )
    problem = ComponentLayoutOptimizationProblem(
        base,
        tuple(component.constraint for component in plan.components),
    )
    validate_component_layout_problem(problem)


def _region_as_area(region: ComponentRegion) -> tuple[float, float, float, float]:
    return (region.min_x, region.max_x, region.min_y, region.max_y)


def _is_internal_wire(wire: LayoutWire, plan: _CompositionPlan) -> bool:
    for component in plan.components:
        member_ids = component.constraint.member_ids
        if wire.source_entity in member_ids and wire.target_entity in member_ids:
            return wire in component.wires
    return False


__all__ = ["synthesize_provider_component_layout"]
