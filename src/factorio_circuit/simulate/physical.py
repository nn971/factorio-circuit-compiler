"""Tick-accurate simulator for the current physical Factorio circuit subset."""

from __future__ import annotations

from collections import defaultdict
from typing import SupportsInt, cast

from factorio_circuit.ir.physical import (
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    Operand,
    PhysicalCircuit,
    SelectorCombinator,
    SignalId,
    WireColor,
    WireEndpoint,
)
from factorio_circuit.target.factorio.semantics import apply_binary, apply_compare, i32
from factorio_circuit.target.factorio.signals import SIGNAL_EACH, SIGNAL_EVERYTHING

NetworkKey = tuple[WireColor, WireEndpoint]
SignalMap = dict[SignalId, int]
InputNetworks = dict[WireColor, SignalMap]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[NetworkKey, NetworkKey] = {}

    def find(self, item: NetworkKey) -> NetworkKey:
        self.parent.setdefault(item, item)
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: NetworkKey, right: NetworkKey) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def simulate_stream(
    circuit: PhysicalCircuit,
    input_stream: list[dict[str, object]],
    *,
    flush_ticks: int | None = None,
) -> list[tuple[object, ...]]:
    """Simulate the deterministic physical circuit subset tick-by-tick.

    Deterministic selector Select-input mode is evaluated here. Nondeterministic/environment
    selector modes remain outside this simulator; Random Input is an oracle implementation and is
    tested through scripted semantic oracle traces or in Factorio itself.
    """

    unsupported_selectors = [
        entity
        for entity in circuit.entities
        if isinstance(entity, SelectorCombinator) and entity.operation != "select"
    ]
    if unsupported_selectors:
        modes = ", ".join(sorted({entity.operation for entity in unsupported_selectors}))
        raise ValueError(
            "deterministic physical simulation does not evaluate selector mode(s) "
            f"{modes}; use a scripted semantic oracle trace or target execution"
        )

    networks = _build_networks(circuit)
    max_phase = max(circuit.output_phases, default=0)
    flush = max_phase if flush_ticks is None else flush_ticks
    total_ticks = len(input_stream) + flush
    pending_outputs: dict[int, SignalMap] = {}
    observations: list[tuple[object, ...]] = []

    for tick_index in range(total_ticks):
        injected = input_stream[tick_index] if tick_index < len(input_stream) else {}
        network_values: dict[NetworkKey, SignalMap] = defaultdict(dict)

        for port in circuit.inputs:
            input_roots = [
                root
                for color in WireColor
                if (
                    root := networks.get(
                        (color, WireEndpoint(port.marker_entity, Connector.SINGLE))
                    )
                )
                is not None
            ]
            raw = injected.get(port.name, 0 if port.signal is not None else {})
            if port.signal is not None:
                value = i32(int(cast(SupportsInt, raw)))
                for input_root in input_roots:
                    _add_signal(network_values[input_root], port.signal, value)
            else:
                if not isinstance(raw, dict):
                    raise ValueError(f"vector input {port.name!r} expects a signal map")
                for signal, value in raw.items():
                    if not isinstance(signal, SignalId):
                        raise ValueError("physical vector simulation expects SignalId keys")
                    for input_root in input_roots:
                        _add_signal(
                            network_values[input_root],
                            signal,
                            i32(int(cast(SupportsInt, value))),
                        )

        for entity in circuit.entities:
            if isinstance(entity, ConstantCombinator) and not entity.annotation_only:
                endpoint = WireEndpoint(entity.id, Connector.SINGLE)
                for color in WireColor:
                    entity_root = networks.get((color, endpoint))
                    if entity_root is None:
                        continue
                    for signal, value in entity.signals:
                        _add_signal(network_values[entity_root], signal, value)

        for entity_id, signals in pending_outputs.items():
            endpoint = WireEndpoint(entity_id, Connector.OUTPUT)
            for color in WireColor:
                output_root = networks.get((color, endpoint))
                if output_root is None:
                    continue
                for signal, value in signals.items():
                    _add_signal(network_values[output_root], signal, value)

        observations.append(
            tuple(
                _read_port_signal(network_values, networks, port.marker_entity, port.signal)
                if port.signal is not None
                else _read_port_map(network_values, networks, port.marker_entity)
                for port in circuit.outputs
            )
        )

        next_outputs: dict[int, SignalMap] = {}
        for entity in circuit.entities:
            if isinstance(entity, SelectorCombinator):
                inputs = _read_input_networks(entity.id, network_values, networks)
                next_outputs[entity.id] = _eval_selector(entity, inputs)
            elif isinstance(entity, ArithmeticCombinator):
                inputs = _read_input_networks(entity.id, network_values, networks)
                next_outputs[entity.id] = _eval_arithmetic(entity, inputs)
            elif isinstance(entity, DeciderCombinator):
                inputs = _read_input_networks(entity.id, network_values, networks)
                next_outputs[entity.id] = _eval_decider(entity, inputs)
        pending_outputs = next_outputs

    return observations


def evaluate(circuit: PhysicalCircuit, inputs: dict[str, int]) -> tuple[object, ...]:
    max_phase = max(circuit.output_phases, default=0)
    input_row: dict[str, object] = dict(inputs)
    observations = simulate_stream(circuit, [input_row] * (max_phase + 1), flush_ticks=max_phase)
    return tuple(observations[port.phase][index] for index, port in enumerate(circuit.outputs))


def _build_networks(circuit: PhysicalCircuit) -> dict[NetworkKey, NetworkKey]:
    uf = _UnionFind()
    for connection in circuit.connections:
        uf.union((connection.color, connection.source), (connection.color, connection.target))

    for port in circuit.inputs:
        uf.find((WireColor.RED, WireEndpoint(port.marker_entity, Connector.SINGLE)))
    for output_port in circuit.outputs:
        uf.find((WireColor.RED, WireEndpoint(output_port.marker_entity, Connector.SINGLE)))

    return {item: uf.find(item) for item in list(uf.parent)}


def _read_input_networks(
    entity_id: int,
    values: dict[NetworkKey, SignalMap],
    networks: dict[NetworkKey, NetworkKey],
) -> InputNetworks:
    result: InputNetworks = {WireColor.RED: {}, WireColor.GREEN: {}}
    endpoint = WireEndpoint(entity_id, Connector.INPUT)
    for color in WireColor:
        root = networks.get((color, endpoint))
        if root is None:
            continue
        result[color] = dict(values.get(root, {}))
    return result


def _read_port_signal(
    values: dict[NetworkKey, SignalMap],
    networks: dict[NetworkKey, NetworkKey],
    entity_id: int,
    signal: SignalId,
) -> int:
    endpoint = WireEndpoint(entity_id, Connector.SINGLE)
    total = 0
    for color in WireColor:
        root = networks.get((color, endpoint))
        if root is not None:
            total = i32(total + values.get(root, {}).get(signal, 0))
    return total


def _read_port_map(
    values: dict[NetworkKey, SignalMap],
    networks: dict[NetworkKey, NetworkKey],
    entity_id: int,
) -> SignalMap:
    endpoint = WireEndpoint(entity_id, Connector.SINGLE)
    result: SignalMap = {}
    for color in WireColor:
        root = networks.get((color, endpoint))
        if root is None:
            continue
        for signal, value in values.get(root, {}).items():
            _add_signal(result, signal, value)
    return result


def _selected_colors(networks: tuple[WireColor, ...] | None) -> tuple[WireColor, ...]:
    return tuple(WireColor) if networks is None else networks


def _combined_inputs(inputs: InputNetworks, networks: tuple[WireColor, ...] | None) -> SignalMap:
    result: SignalMap = {}
    for color in _selected_colors(networks):
        for signal, value in inputs[color].items():
            _add_signal(result, signal, value)
    return result


def _eval_selector(entity: SelectorCombinator, inputs: InputNetworks) -> SignalMap:
    if entity.operation != "select":
        raise ValueError(f"unsupported deterministic selector operation {entity.operation!r}")
    vector = _combined_inputs(inputs, None)
    if not vector:
        return {}
    ordered = sorted(
        vector.items(),
        key=lambda item: (item[1], item[0].kind, item[0].name),
        reverse=entity.select_max,
    )
    if len(ordered) == 1:
        signal, amount = ordered[0]
        return {signal: amount}
    if entity.index >= len(ordered):
        return {}
    signal, amount = ordered[entity.index]
    return {signal: amount}


def _eval_arithmetic(entity: ArithmeticCombinator, inputs: InputNetworks) -> SignalMap:
    if entity.output_each:
        if not entity.left.each and not entity.right.each:
            raise ValueError("Each output requires an Each input operand")
        result: SignalMap = {}
        if entity.left.each and entity.right.each:
            left_inputs = _combined_inputs(inputs, entity.left.networks)
            right_inputs = _combined_inputs(inputs, entity.right.networks)
            for signal in left_inputs.keys() | right_inputs.keys():
                output = apply_binary(
                    entity.operation,
                    left_inputs.get(signal, 0),
                    right_inputs.get(signal, 0),
                )
                if output != 0:
                    result[signal] = output
            return result

        each_operand = entity.left if entity.left.each else entity.right
        other = entity.right if entity.left.each else entity.left
        lane_inputs = _combined_inputs(inputs, each_operand.networks)
        for signal, value in lane_inputs.items():
            other_value = _read_operand(other, inputs)
            output = apply_binary(
                entity.operation,
                value if entity.left.each else other_value,
                other_value if entity.left.each else value,
            )
            if output != 0:
                result[signal] = output
        return result

    if entity.output_signal is None:
        raise ValueError("scalar arithmetic combinator has no output signal")
    output = apply_binary(
        entity.operation,
        _read_operand(entity.left, inputs),
        _read_operand(entity.right, inputs),
    )
    return {} if output == 0 else {entity.output_signal: output}


def _eval_decider(entity: DeciderCombinator, inputs: InputNetworks) -> SignalMap:
    conditions = [
        (entity.comparator, entity.left, entity.right, None),
        *(
            (condition.comparator, condition.left, condition.right, condition.compare_type)
            for condition in entity.additional_conditions
        ),
    ]
    outputs = [
        (
            entity.output_signal,
            entity.output_copy_count_from_input,
            entity.output_constant,
            entity.output_networks,
        ),
        *(
            (
                output.signal,
                output.copy_count_from_input,
                output.constant,
                output.output_networks,
            )
            for output in entity.additional_outputs
        ),
    ]
    each_operands = [
        operand
        for _comparator, left, right, _compare_type in conditions
        for operand in (left, right)
        if operand.each
    ]

    if each_operands:
        lanes: set[SignalId] = set()
        for operand in each_operands:
            lanes.update(_combined_inputs(inputs, operand.networks))
        result: SignalMap = {}
        for lane in lanes:
            if not _decider_conditions_match(conditions, inputs, each_signal=lane):
                continue
            for signal, copy_count, constant, networks in outputs:
                value = _read_signal(lane, inputs, networks) if copy_count else i32(constant)
                _add_signal(result, lane if signal == SIGNAL_EACH else signal, value)
        return result

    if _decider_conditions_match(conditions, inputs):
        matched_result: SignalMap = {}
        for signal, copy_count, constant, networks in outputs:
            for output_signal, value in _decider_output(
                signal, copy_count, constant, networks, inputs
            ).items():
                _add_signal(matched_result, output_signal, value)
        return matched_result
    if entity.else_output_signal is not None:
        return _decider_output(
            entity.else_output_signal,
            entity.else_copy_count_from_input,
            entity.else_output_constant,
            entity.else_output_networks,
            inputs,
        )
    return {}


def _decider_conditions_match(
    conditions: list[tuple[str, Operand, Operand, str | None]],
    inputs: InputNetworks,
    *,
    each_signal: SignalId | None = None,
) -> bool:
    result: bool | None = None
    for comparator, left, right, compare_type in conditions:
        current = apply_compare(
            comparator,
            _read_decider_operand(left, inputs, each_signal),
            _read_decider_operand(right, inputs, each_signal),
        )
        if result is None:
            result = current
        elif compare_type == "or":
            result = result or current
        else:
            result = result and current
    return bool(result)


def _read_decider_operand(
    operand: Operand, inputs: InputNetworks, each_signal: SignalId | None
) -> int:
    if not operand.each:
        return _read_operand(operand, inputs)
    if each_signal is None:
        raise ValueError("Each decider condition requires per-signal evaluation")
    return _read_signal(each_signal, inputs, operand.networks)


def _decider_output(
    signal: SignalId,
    copy_count: bool,
    constant: int,
    networks: tuple[WireColor, ...] | None,
    inputs: InputNetworks,
) -> SignalMap:
    if signal == SIGNAL_EVERYTHING:
        selected = _combined_inputs(inputs, networks)
        if copy_count:
            return selected
        value = i32(constant)
        if value == 0:
            return {}
        return {lane: value for lane in selected}
    value = _read_signal(signal, inputs, networks) if copy_count else i32(constant)
    return {} if value == 0 else {signal: value}


def _read_signal(
    signal: SignalId,
    inputs: InputNetworks,
    networks: tuple[WireColor, ...] | None,
) -> int:
    total = 0
    for color in _selected_colors(networks):
        total = i32(total + inputs[color].get(signal, 0))
    return total


def _read_operand(operand: Operand, inputs: InputNetworks) -> int:
    if operand.signal is not None:
        return _read_signal(operand.signal, inputs, operand.networks)
    if operand.constant is not None:
        return i32(operand.constant)
    raise ValueError("Each operand cannot be read as a scalar")


def _add_signal(target: SignalMap, signal: SignalId, value: int) -> None:
    result = i32(target.get(signal, 0) + value)
    if result == 0:
        target.pop(signal, None)
    else:
        target[signal] = result
