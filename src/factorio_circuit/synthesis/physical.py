"""Baseline joint physical synthesis for the abstract physical IR."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
from itertools import combinations
from math import ceil, hypot

from factorio_circuit.blueprint.routing import (
    DEFAULT_SAFE_WIRE_SPAN,
    route_wires,
    routed_positions,
)
from factorio_circuit.ir import abstract_physical as abstract
from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    DeciderCondition,
    InputPort,
    Operand,
    OutputPort,
    PhysicalCircuit,
    SignalId,
    WireColor,
    WireConnection,
    WireEndpoint,
)
from factorio_circuit.synthesis.layout import Layout, LayoutRelay, LayoutWire
from factorio_circuit.synthesis.placement import PlacementOptions, plan_physical_circuit
from factorio_circuit.target.factorio.signals import DEFAULT_VIRTUAL_SIGNAL_POOL


@dataclass(slots=True)
class _UnionFind:
    parent: dict[int, int]

    @classmethod
    def for_items(cls, items: list[int]) -> _UnionFind:
        return cls({item: item for item in items})

    def find(self, item: int) -> int:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        self.parent[high] = low


@dataclass(slots=True)
class PhysicalSynthesizer:
    circuit: abstract.AbstractPhysicalCircuit
    safe_wire_span: float = DEFAULT_SAFE_WIRE_SPAN
    signal_pool: tuple[SignalId, ...] = DEFAULT_VIRTUAL_SIGNAL_POOL
    placement_options: PlacementOptions | None = None

    def synthesize(self) -> Layout:
        self.circuit.validate()
        net_colors = self._assign_net_colors()
        net_groups = self._coalesce_shared_connector_nets(net_colors)
        signal_allocation = self._allocate_signals(net_groups)
        physical = self._materialize_circuit(signal_allocation, net_colors)

        selected = self.placement_options or PlacementOptions()
        selected.validate()
        attempts = selected.restarts
        if selected.strategy == "row" or selected.iterations == 0:
            attempts = 1

        last_routing_error: ValueError | None = None
        for restart in range(attempts):
            attempt_options = replace(
                selected,
                random_seed=selected.random_seed + restart,
                target_fill=selected.target_fill * selected.retry_fill_scale**restart,
                restarts=1,
            )
            placement = plan_physical_circuit(
                physical,
                self.circuit,
                net_groups,
                safe_wire_span=self.safe_wire_span,
                options=attempt_options,
            )
            positions = placement.positions
            self._materialize_connections(physical, net_colors, net_groups, positions)
            try:
                routing = route_wires(
                    physical,
                    positions,
                    safe_span=self.safe_wire_span,
                    relay_forbidden_areas=placement.relay_forbidden_areas,
                )
            except ValueError as exc:
                if "parallel lanes and grid search were both exhausted" not in str(exc):
                    raise
                last_routing_error = exc
                continue

            final_positions = routed_positions(physical, positions, routing)
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

    def _allocate_signals(self, net_groups: dict[int, int]) -> dict[int, SignalId]:
        """Color signal-alias classes over the concrete Factorio signal pool."""

        reserved = self._fixed_signal_ids()
        available = tuple(signal for signal in self.signal_pool if signal not in reserved)
        if not available and self.circuit.signals:
            raise ValueError(
                "physical synthesis has no concrete virtual signals available for allocation"
            )

        alias_roots = self._signal_alias_roots()
        members_by_root: dict[int, list[int]] = defaultdict(list)
        for signal in self.circuit.signals:
            members_by_root[alias_roots[signal.id]].append(signal.id)

        signal_groups: dict[int, set[int]] = {root: set() for root in members_by_root}
        group_members: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
        for net in self.circuit.nets:
            if net.carries_dynamic_vector and net.signals:
                raise ValueError(
                    f"runtime-open vector net {net.id} cannot carry compiler-allocated "
                    "abstract signal lanes"
                )
            group = net_groups[net.id]
            for signal_id in net.signals:
                root = alias_roots[signal_id]
                signal_groups[root].add(group)
                group_members[group][root].add(signal_id)

        for group, by_root in group_members.items():
            collapsed = [sorted(members) for members in by_root.values() if len(members) > 1]
            if collapsed:
                raise ValueError(
                    "signal-alias constraint would collapse distinct lanes on synthesized "
                    f"electrical group {group}: {collapsed}"
                )

        adjacency: dict[int, set[int]] = {root: set() for root in members_by_root}
        for conflict in self.circuit.signal_conflicts:
            left = alias_roots[conflict.left]
            right = alias_roots[conflict.right]
            if left == right:
                raise ValueError(
                    "signal alias class contains a pair that is also required to conflict"
                )
            adjacency[left].add(right)
            adjacency[right].add(left)

        roots = sorted(adjacency)
        for index, left in enumerate(roots):
            for right in roots[index + 1 :]:
                if signal_groups[left] & signal_groups[right]:
                    adjacency[left].add(right)
                    adjacency[right].add(left)

        by_id = {signal.id: signal for signal in self.circuit.signals}
        order = sorted(roots, key=lambda root: (-len(adjacency[root]), root))
        root_allocation: dict[int, SignalId] = {}
        for root in order:
            for signal_id in members_by_root[root]:
                domain = by_id[signal_id].domain
                if domain not in {abstract.SignalDomain.ANY, abstract.SignalDomain.VIRTUAL}:
                    raise ValueError(
                        f"baseline physical synthesis cannot allocate {domain.value} signals yet"
                    )
            forbidden = {
                root_allocation[neighbor]
                for neighbor in adjacency[root]
                if neighbor in root_allocation
            }
            concrete = next((item for item in available if item not in forbidden), None)
            if concrete is None:
                raise ValueError(
                    "physical synthesis exhausted the concrete virtual-signal pool while "
                    "coloring the abstract signal-interference graph"
                )
            root_allocation[root] = concrete

        return {
            signal.id: root_allocation[alias_roots[signal.id]] for signal in self.circuit.signals
        }

    def _signal_alias_roots(self) -> dict[int, int]:
        signal_ids = [signal.id for signal in self.circuit.signals]
        groups = _UnionFind.for_items(signal_ids)
        for alias in self.circuit.signal_aliases:
            groups.union(alias.left, alias.right)
        return {signal_id: groups.find(signal_id) for signal_id in signal_ids}

    def _fixed_signal_ids(self) -> set[SignalId]:
        result = {signal for net in self.circuit.nets for signal in net.fixed_signals}
        for entity in self.circuit.entities:
            if isinstance(entity, abstract.ConstantCombinator):
                for signal, _count in entity.signals:
                    if isinstance(signal, SignalId):
                        result.add(signal)
            elif isinstance(entity, (abstract.ArithmeticCombinator, abstract.DeciderCombinator)):
                operands = [entity.left, entity.right]
                if isinstance(entity, abstract.DeciderCombinator):
                    operands.extend(
                        operand
                        for condition in entity.additional_conditions
                        for operand in (condition.left, condition.right)
                    )
                for operand in operands:
                    if isinstance(operand.signal, SignalId):
                        result.add(operand.signal)
        return result

    def _assign_net_colors(self) -> dict[int, WireColor]:
        """Choose two wire colors while maximizing proven-safe local coalescing.

        Explicit ``NetConflict`` metadata is only the starting point.  Physical synthesis
        also rejects a same-color merge when two nets share known lanes or when either is
        a runtime-open vector net.  Because same-color merges are transitive through shared
        connectors, unsafe aggregate groups discovered after coloring are fed back as new
        hard constraints until every synthesized electrical group is proven compatible.
        """

        hard_conflicts = {
            self._pair(conflict.left, conflict.right) for conflict in self.circuit.net_conflicts
        }
        preferences, local_conflicts = self._shared_connector_relations()
        hard_conflicts.update(local_conflicts)

        while True:
            colors = self._color_net_constraints(hard_conflicts, preferences)
            unsafe = self._unsafe_group_conflicts(colors) - hard_conflicts
            if not unsafe:
                return colors
            hard_conflicts.update(unsafe)

    def _color_net_constraints(
        self,
        hard_conflicts: set[tuple[int, int]],
        preferences: dict[tuple[int, int], int],
    ) -> dict[int, WireColor]:
        adjacency: dict[int, set[int]] = {net.id: set() for net in self.circuit.nets}
        for left, right in hard_conflicts:
            adjacency[left].add(right)
            adjacency[right].add(left)

        component_of: dict[int, int] = {}
        parity: dict[int, int] = {}
        components: list[list[int]] = []
        for start in sorted(adjacency):
            if start in component_of:
                continue
            component_id = len(components)
            component: list[int] = []
            component_of[start] = component_id
            parity[start] = 0
            queue = deque([start])
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbor in sorted(adjacency[current]):
                    expected = parity[current] ^ 1
                    if neighbor not in component_of:
                        component_of[neighbor] = component_id
                        parity[neighbor] = expected
                        queue.append(neighbor)
                    elif parity[neighbor] != expected:
                        raise ValueError(
                            "abstract net constraints require more than the two Factorio "
                            "wire colors"
                        )
            components.append(sorted(component))

        flips = [0] * len(components)
        improved = True
        while improved:
            improved = False
            for component_id in range(len(components)):
                before = self._coalescing_score(
                    preferences, component_of, parity, flips, component_id
                )
                flips[component_id] ^= 1
                after = self._coalescing_score(
                    preferences, component_of, parity, flips, component_id
                )
                if after > before:
                    improved = True
                else:
                    flips[component_id] ^= 1

        return {
            net.id: (
                WireColor.RED
                if (parity[net.id] ^ flips[component_of[net.id]]) == 0
                else WireColor.GREEN
            )
            for net in self.circuit.nets
        }

    def _shared_connector_relations(
        self,
    ) -> tuple[dict[tuple[int, int], int], set[tuple[int, int]]]:
        endpoint_nets: dict[abstract.Endpoint, list[int]] = defaultdict(list)
        for net in self.circuit.nets:
            for endpoint in net.endpoints:
                endpoint_nets[endpoint].append(net.id)

        preferences: dict[tuple[int, int], int] = defaultdict(int)
        conflicts: set[tuple[int, int]] = set()
        for net_ids in endpoint_nets.values():
            for left, right in combinations(sorted(set(net_ids)), 2):
                pair = self._pair(left, right)
                if self._nets_can_coalesce_locally(left, right):
                    preferences[pair] += 1
                else:
                    conflicts.add(pair)
        return dict(preferences), conflicts

    def _nets_can_coalesce_locally(self, left_id: int, right_id: int) -> bool:
        explicit = {
            self._pair(conflict.left, conflict.right) for conflict in self.circuit.net_conflicts
        }
        if self._pair(left_id, right_id) in explicit:
            return False
        left = self.circuit.net_by_id(left_id)
        right = self.circuit.net_by_id(right_id)
        if left.carries_dynamic_vector or right.carries_dynamic_vector:
            return False
        alias_roots = self._signal_alias_roots()
        left_aliases = {alias_roots[signal] for signal in left.signals}
        right_aliases = {alias_roots[signal] for signal in right.signals}
        if left_aliases & right_aliases:
            return False
        return not set(left.fixed_signals) & set(right.fixed_signals)

    def _unsafe_group_conflicts(self, net_colors: dict[int, WireColor]) -> set[tuple[int, int]]:
        groups = self._raw_net_groups(net_colors)
        members: dict[int, list[int]] = defaultdict(list)
        for net_id, group in groups.items():
            members[group].append(net_id)

        explicit = {
            self._pair(conflict.left, conflict.right) for conflict in self.circuit.net_conflicts
        }
        unsafe: set[tuple[int, int]] = set()
        alias_roots = self._signal_alias_roots()
        for net_ids in members.values():
            for left_id, right_id in combinations(sorted(net_ids), 2):
                pair = self._pair(left_id, right_id)
                left = self.circuit.net_by_id(left_id)
                right = self.circuit.net_by_id(right_id)
                left_aliases = {alias_roots[signal] for signal in left.signals}
                right_aliases = {alias_roots[signal] for signal in right.signals}
                if (
                    pair in explicit
                    or left.carries_dynamic_vector
                    or right.carries_dynamic_vector
                    or left_aliases & right_aliases
                    or set(left.fixed_signals) & set(right.fixed_signals)
                ):
                    unsafe.add(pair)
        return unsafe

    @staticmethod
    def _coalescing_score(
        preferences: dict[tuple[int, int], int],
        component_of: dict[int, int],
        parity: dict[int, int],
        flips: list[int],
        component_id: int,
    ) -> int:
        score = 0
        for (left, right), weight in preferences.items():
            if component_of[left] != component_id and component_of[right] != component_id:
                continue
            left_color = parity[left] ^ flips[component_of[left]]
            right_color = parity[right] ^ flips[component_of[right]]
            if left_color == right_color:
                score += weight
        return score

    def _raw_net_groups(self, net_colors: dict[int, WireColor]) -> dict[int, int]:
        net_ids = [net.id for net in self.circuit.nets]
        groups = _UnionFind.for_items(net_ids)
        endpoint_nets: dict[abstract.Endpoint, list[int]] = defaultdict(list)
        for net in self.circuit.nets:
            for endpoint in net.endpoints:
                endpoint_nets[endpoint].append(net.id)

        for net_ids_at_endpoint in endpoint_nets.values():
            by_color: dict[WireColor, list[int]] = defaultdict(list)
            for net_id in net_ids_at_endpoint:
                by_color[net_colors[net_id]].append(net_id)
            for same_color in by_color.values():
                if len(same_color) < 2:
                    continue
                root = min(same_color)
                for net_id in same_color[1:]:
                    groups.union(root, net_id)
        return {net_id: groups.find(net_id) for net_id in net_ids}

    @staticmethod
    def _pair(left: int, right: int) -> tuple[int, int]:
        return (left, right) if left < right else (right, left)

    def _coalesce_shared_connector_nets(self, net_colors: dict[int, WireColor]) -> dict[int, int]:
        """Return the proven-safe physical electrical groups for the chosen colors."""

        unsafe = self._unsafe_group_conflicts(net_colors)
        if unsafe:  # pragma: no cover - guarded by _assign_net_colors
            raise AssertionError(f"unsafe synthesized net group(s): {sorted(unsafe)}")
        return self._raw_net_groups(net_colors)

    def _materialize_circuit(
        self,
        signals: dict[int, SignalId],
        net_colors: dict[int, WireColor],
    ) -> PhysicalCircuit:
        physical = PhysicalCircuit(name=self.circuit.name)
        annotation_descriptions = self._annotation_descriptions(signals)
        for entity in self.circuit.entities:
            physical.entities.append(
                self._materialize_entity(entity, signals, net_colors, annotation_descriptions)
            )

        for port in self.circuit.inputs:
            physical.inputs.append(
                InputPort(
                    port.name,
                    port.endpoint.entity,
                    None if port.signal is None else self._signal_ref(port.signal, signals),
                )
            )
        for output_port in self.circuit.outputs:
            physical.outputs.append(
                OutputPort(
                    output_port.name,
                    output_port.endpoint.entity,
                    (
                        None
                        if output_port.signal is None
                        else self._signal_ref(output_port.signal, signals)
                    ),
                    output_port.phase,
                )
            )
        return physical

    def _materialize_connections(
        self,
        physical: PhysicalCircuit,
        net_colors: dict[int, WireColor],
        net_groups: dict[int, int],
        positions: dict[int, tuple[float, float]],
    ) -> None:
        """Choose a geometry-aware spanning tree for every synthesized physical net."""

        endpoints_by_group: dict[int, set[abstract.Endpoint]] = defaultdict(set)
        colors_by_group: dict[int, WireColor] = {}
        for net in self.circuit.nets:
            group = net_groups[net.id]
            endpoints_by_group[group].update(net.endpoints)
            color = net_colors[net.id]
            previous = colors_by_group.setdefault(group, color)
            if previous != color:  # pragma: no cover - net grouping guarantees this
                raise AssertionError(f"physical net group {group} contains multiple wire colors")

        physical.connections.clear()
        connection_keys: set[tuple[WireEndpoint, WireEndpoint, WireColor]] = set()
        for group in sorted(endpoints_by_group):
            endpoints = tuple(sorted(endpoints_by_group[group]))
            color = colors_by_group[group]
            for left, right in self._minimum_relay_spanning_tree(endpoints, positions):
                source = self._endpoint(left)
                target = self._endpoint(right)
                if source == target:
                    continue
                key = (source, target, color)
                reverse = (target, source, color)
                if key in connection_keys or reverse in connection_keys:
                    continue
                connection_keys.add(key)
                physical.connections.append(WireConnection(source, target, color))

    def _minimum_relay_spanning_tree(
        self,
        endpoints: tuple[abstract.Endpoint, ...],
        positions: dict[int, tuple[float, float]],
    ) -> tuple[tuple[abstract.Endpoint, abstract.Endpoint], ...]:
        if len(endpoints) < 2:
            return ()

        connected = {0}
        remaining = set(range(1, len(endpoints)))
        result: list[tuple[abstract.Endpoint, abstract.Endpoint]] = []
        while remaining:
            best: (
                tuple[tuple[int, float, abstract.Endpoint, abstract.Endpoint], int, int] | None
            ) = None
            for left_index in connected:
                left = endpoints[left_index]
                left_position = positions[left.entity]
                for right_index in remaining:
                    right = endpoints[right_index]
                    right_position = positions[right.entity]
                    distance = hypot(
                        left_position[0] - right_position[0],
                        left_position[1] - right_position[1],
                    )
                    relay_count = max(
                        0,
                        ceil(distance / self.safe_wire_span - 1e-12) - 1,
                    )
                    key = (relay_count, distance, left, right)
                    if best is None or key < best[0]:
                        best = (key, left_index, right_index)

            assert best is not None
            _, left_index, right_index = best
            result.append((endpoints[left_index], endpoints[right_index]))
            connected.add(right_index)
            remaining.remove(right_index)

        return tuple(result)

    def _annotation_descriptions(self, signals: dict[int, SignalId]) -> dict[int, str]:
        descriptions: dict[int, str] = {}
        for port in self.circuit.inputs:
            if port.signal is not None:
                concrete = self._signal_ref(port.signal, signals)
                descriptions[port.endpoint.entity] = (
                    f"INPUT {port.name} — inject value on [{concrete.name}] here"
                )
        for output_port in self.circuit.outputs:
            if output_port.signal is not None:
                concrete = self._signal_ref(output_port.signal, signals)
                descriptions[output_port.endpoint.entity] = (
                    f"OUTPUT {output_port.name} — [{concrete.name}], "
                    f"phase +{output_port.phase} tick(s)"
                )
        return descriptions

    def _materialize_entity(
        self,
        entity: abstract.AbstractEntity,
        signals: dict[int, SignalId],
        net_colors: dict[int, WireColor],
        annotation_descriptions: dict[int, str],
    ) -> ArithmeticCombinator | DeciderCombinator | ConstantCombinator:
        if isinstance(entity, abstract.ArithmeticCombinator):
            return ArithmeticCombinator(
                id=entity.id,
                operation=entity.operation,
                left=self._operand(entity.left, signals, net_colors),
                right=self._operand(entity.right, signals, net_colors),
                output_each=entity.output_each,
                output_signal=(
                    None if entity.output_signal is None else signals[entity.output_signal]
                ),
                description=entity.description,
            )
        if isinstance(entity, abstract.DeciderCombinator):
            return DeciderCombinator(
                id=entity.id,
                comparator=entity.comparator,
                left=self._operand(entity.left, signals, net_colors),
                right=self._operand(entity.right, signals, net_colors),
                output_signal=signals[entity.output_signal],
                output_constant=entity.output_constant,
                output_copy_count_from_input=entity.output_copy_count_from_input,
                output_networks=self._network_selection(entity.copy_count_nets, net_colors),
                additional_conditions=tuple(
                    DeciderCondition(
                        comparator=condition.comparator,
                        left=self._operand(condition.left, signals, net_colors),
                        right=self._operand(condition.right, signals, net_colors),
                        compare_type=condition.compare_type,
                    )
                    for condition in entity.additional_conditions
                ),
                else_output_signal=(
                    None
                    if entity.else_output_signal is None
                    else signals[entity.else_output_signal]
                ),
                else_output_constant=entity.else_output_constant,
                else_copy_count_from_input=entity.else_copy_count_from_input,
                else_output_networks=self._network_selection(
                    entity.else_copy_count_nets, net_colors
                ),
                description=entity.description,
            )
        return ConstantCombinator(
            id=entity.id,
            signals=tuple(
                (self._signal_ref(signal, signals), count) for signal, count in entity.signals
            ),
            description=annotation_descriptions.get(entity.id, entity.description),
            annotation_only=entity.annotation_only,
        )

    def _operand(
        self,
        operand: abstract.Operand,
        signals: dict[int, SignalId],
        net_colors: dict[int, WireColor],
    ) -> Operand:
        return Operand(
            signal=(None if operand.signal is None else self._signal_ref(operand.signal, signals)),
            constant=operand.constant,
            each=operand.each,
            networks=self._network_selection(operand.nets, net_colors),
        )

    @staticmethod
    def _signal_ref(signal: abstract.SignalRef, signals: dict[int, SignalId]) -> SignalId:
        return signals[signal] if isinstance(signal, int) else signal

    @staticmethod
    def _network_selection(
        nets: tuple[int, ...], net_colors: dict[int, WireColor]
    ) -> tuple[WireColor, ...] | None:
        if not nets:
            return None
        selected: list[WireColor] = []
        for net in nets:
            color = net_colors[net]
            if color not in selected:
                selected.append(color)
        return tuple(selected)

    @staticmethod
    def _endpoint(endpoint: abstract.Endpoint) -> WireEndpoint:
        return WireEndpoint(endpoint.entity, Connector(endpoint.connector.value))


def synthesize_layout(
    circuit: abstract.AbstractPhysicalCircuit,
    *,
    safe_wire_span: float = DEFAULT_SAFE_WIRE_SPAN,
    placement: PlacementOptions | None = None,
) -> Layout:
    """Materialize a concrete, reach-safe layout from abstract physical IR."""

    return PhysicalSynthesizer(
        circuit,
        safe_wire_span=safe_wire_span,
        placement_options=placement,
    ).synthesize()
