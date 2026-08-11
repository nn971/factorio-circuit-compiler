"""Baseline joint physical synthesis for the abstract physical IR."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import combinations

from factorio_circuit.blueprint.layout import row_positions
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

    def synthesize(self) -> Layout:
        self.circuit.validate()
        signal_allocation = self._allocate_signals()
        net_colors = self._assign_net_colors()
        net_groups = self._coalesce_shared_connector_nets(net_colors)
        physical = self._materialize_circuit(signal_allocation, net_colors)
        positions = row_positions(physical)
        routing = route_wires(physical, positions, safe_span=self.safe_wire_span)
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

    def _allocate_signals(self) -> dict[int, SignalId]:
        """Allocate compiler lanes while avoiding user-fixed concrete signals."""

        reserved = self._fixed_signal_ids()
        available = tuple(signal for signal in self.signal_pool if signal not in reserved)
        if len(self.circuit.signals) > len(available):
            raise ValueError(
                "baseline physical synthesis exhausted the concrete virtual-signal pool"
            )
        result: dict[int, SignalId] = {}
        for signal, concrete in zip(self.circuit.signals, available, strict=False):
            if signal.domain not in {abstract.SignalDomain.ANY, abstract.SignalDomain.VIRTUAL}:
                raise ValueError(
                    f"baseline physical synthesis cannot allocate {signal.domain.value} signals yet"
                )
            result[signal.id] = concrete
        return result

    def _fixed_signal_ids(self) -> set[SignalId]:
        result = {signal for net in self.circuit.nets for signal in net.fixed_signals}
        for entity in self.circuit.entities:
            if isinstance(entity, abstract.ConstantCombinator):
                for signal, _count in entity.signals:
                    if isinstance(signal, SignalId):
                        result.add(signal)
            elif isinstance(entity, (abstract.ArithmeticCombinator, abstract.DeciderCombinator)):
                for operand in (entity.left, entity.right):
                    if isinstance(operand.signal, SignalId):
                        result.add(operand.signal)
        return result

    def _assign_net_colors(self) -> dict[int, WireColor]:
        """Two-color hard conflicts while preferring shared-connector net coalescing.

        Every connected component of the hard conflict graph has two equivalent color
        orientations.  We first bipartition each component, then greedily flip whole
        components when that increases the number of same-color net pairs already meeting
        at a connector.  The objective is deliberately local and deterministic; it is the
        first synthesis optimization rather than a claim of globally optimal coloring.
        """

        adjacency: dict[int, set[int]] = {net.id: set() for net in self.circuit.nets}
        for conflict in self.circuit.net_conflicts:
            adjacency[conflict.left].add(conflict.right)
            adjacency[conflict.right].add(conflict.left)

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
                            "abstract net conflicts require more than the two Factorio wire colors"
                        )
            components.append(sorted(component))

        preferences = self._shared_connector_preferences()
        flips = [0] * len(components)

        # Deterministic coordinate-ascent on component polarity.  A flip is accepted only
        # for a strict improvement, so ties keep the baseline red-first orientation.
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

    def _shared_connector_preferences(self) -> dict[tuple[int, int], int]:
        endpoint_nets: dict[abstract.Endpoint, list[int]] = defaultdict(list)
        for net in self.circuit.nets:
            for endpoint in net.endpoints:
                endpoint_nets[endpoint].append(net.id)

        weights: dict[tuple[int, int], int] = defaultdict(int)
        for net_ids in endpoint_nets.values():
            for left, right in combinations(sorted(set(net_ids)), 2):
                weights[(left, right)] += 1
        return dict(weights)

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

    def _coalesce_shared_connector_nets(
        self, net_colors: dict[int, WireColor]
    ) -> dict[int, int]:
        """Make unavoidable same-color merges at shared connectors explicit.

        Factorio has only one red and one green circuit network per connector.  Therefore
        two abstract nets touching the same connector with the same selected color are one
        physical electrical network whether or not the IR listed them separately.  Record
        that equivalence explicitly and materialize each group once.
        """

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
                if not same_color:
                    continue
                root = min(same_color)
                for net_id in same_color:
                    groups.union(root, net_id)

        return {net_id: groups.find(net_id) for net_id in net_ids}

    def _materialize_circuit(
        self,
        signals: dict[int, SignalId],
        net_colors: dict[int, WireColor],
    ) -> PhysicalCircuit:
        physical = PhysicalCircuit(name=self.circuit.name)
        annotation_descriptions = self._annotation_descriptions(signals)
        for entity in self.circuit.entities:
            physical.entities.append(
                self._materialize_entity(
                    entity, signals, net_colors, annotation_descriptions
                )
            )

        # Preserve each abstract net's local wiring tree.  Nets placed on the same
        # color and sharing a connector coalesce electrically through that connector;
        # rewiring an entire coalesced group as one global star could create longer
        # spans and extra relays even though the electrical relation is equivalent.
        connection_keys: set[tuple[WireEndpoint, WireEndpoint, WireColor]] = set()
        for net in self.circuit.nets:
            endpoints = [self._endpoint(endpoint) for endpoint in net.endpoints]
            if len(endpoints) < 2:
                continue
            root = endpoints[0]
            for target in endpoints[1:]:
                if root == target:
                    continue
                color = net_colors[net.id]
                key = (root, target, color)
                reverse = (target, root, color)
                if key in connection_keys or reverse in connection_keys:
                    continue
                connection_keys.add(key)
                physical.connections.append(WireConnection(root, target, color))

        for port in self.circuit.inputs:
            physical.inputs.append(
                InputPort(
                    port.name,
                    port.endpoint.entity,
                    None if port.signal is None else signals[port.signal],
                )
            )
        for port in self.circuit.outputs:
            physical.outputs.append(
                OutputPort(
                    port.name,
                    port.endpoint.entity,
                    None if port.signal is None else signals[port.signal],
                    port.phase,
                )
            )
        return physical

    def _annotation_descriptions(
        self, signals: dict[int, SignalId]
    ) -> dict[int, str]:
        descriptions: dict[int, str] = {}
        for port in self.circuit.inputs:
            if port.signal is not None:
                concrete = signals[port.signal]
                descriptions[port.endpoint.entity] = (
                    f"INPUT {port.name} — inject value on [{concrete.name}] here"
                )
        for port in self.circuit.outputs:
            if port.signal is not None:
                concrete = signals[port.signal]
                descriptions[port.endpoint.entity] = (
                    f"OUTPUT {port.name} — [{concrete.name}], "
                    f"phase +{port.phase} tick(s)"
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
                (self._signal_ref(signal, signals), count)
                for signal, count in entity.signals
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
            signal=(
                None
                if operand.signal is None
                else self._signal_ref(operand.signal, signals)
            ),
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
) -> Layout:
    """Materialize a conservative concrete, reach-safe layout from abstract physical IR."""

    return PhysicalSynthesizer(circuit, safe_wire_span=safe_wire_span).synthesize()
