from collections import deque

import pytest

from examples.autonomous_mall.worker_pool import build_worker_pool
from factorio_circuit import compiler as compiler_module
from factorio_circuit import lower_to_abstract_physical
from factorio_circuit.ir.abstract_physical import AbstractPhysicalCircuit
from factorio_circuit.ir.physical import PhysicalCircuit
from factorio_circuit.synthesis.layout import Layout
from factorio_circuit.synthesis.open_vector import VectorPhysicalSynthesizer

_TWO_WIRE_COLOR_ERROR = "abstract net constraints require more than the two Factorio wire colors"


def _odd_cycle_labels(synthesizer: VectorPhysicalSynthesizer) -> list[str]:
    circuit = synthesizer.circuit
    hard = {
        synthesizer._pair(conflict.left, conflict.right)
        for conflict in circuit.net_conflicts
    }
    preferences, local = synthesizer._shared_connector_relations()
    hard.update(local)

    while True:
        adjacency = {net.id: set() for net in circuit.nets}
        for left, right in hard:
            adjacency[left].add(right)
            adjacency[right].add(left)

        color: dict[int, int] = {}
        parent: dict[int, int | None] = {}
        for start in sorted(adjacency):
            if start in color:
                continue
            color[start] = 0
            parent[start] = None
            queue = deque([start])
            while queue:
                current = queue.popleft()
                for neighbor in sorted(adjacency[current]):
                    if neighbor not in color:
                        color[neighbor] = color[current] ^ 1
                        parent[neighbor] = current
                        queue.append(neighbor)
                        continue
                    if color[neighbor] != color[current]:
                        continue

                    left_path: list[int] = []
                    cursor: int | None = current
                    while cursor is not None:
                        left_path.append(cursor)
                        cursor = parent[cursor]
                    right_path: list[int] = []
                    cursor = neighbor
                    while cursor is not None:
                        right_path.append(cursor)
                        cursor = parent[cursor]
                    right_positions = {node: index for index, node in enumerate(right_path)}
                    common = next(node for node in left_path if node in right_positions)
                    left_prefix = left_path[: left_path.index(common) + 1]
                    right_prefix = right_path[: right_positions[common]]
                    cycle = [*left_prefix, *reversed(right_prefix)]
                    return [
                        f"{net_id}:{circuit.net_by_id(net_id).label}"
                        for net_id in cycle
                    ]

        colors = synthesizer._color_net_constraints(hard, preferences)
        unsafe = synthesizer._unsafe_group_conflicts(colors) - hard
        if not unsafe:
            return []
        hard.update(unsafe)


def _contains_packed_entity(circuit: AbstractPhysicalCircuit) -> bool:
    return any("packed " in (entity.description or "") for entity in circuit.entities)


@pytest.mark.parametrize("worker_count", [1, 2])
def test_worker_pool_unpacked_net_constraints_are_two_colorable(worker_count: int) -> None:
    lowered = lower_to_abstract_physical(build_worker_pool(worker_count), optimize=False)
    synthesizer = VectorPhysicalSynthesizer(lowered.abstract_physical)
    cycle = _odd_cycle_labels(synthesizer)
    assert not cycle, "odd wire-color conflict cycle: " + " -> ".join(cycle)


def test_two_worker_packing_currently_creates_an_odd_wire_color_cycle() -> None:
    lowered = lower_to_abstract_physical(build_worker_pool(2), optimize=True)
    synthesizer = VectorPhysicalSynthesizer(lowered.abstract_physical)
    cycle = _odd_cycle_labels(synthesizer)
    assert cycle
    assert any("packed pairwise + output" in label for label in cycle)


def test_compile_circuit_retries_without_packing_after_wire_color_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[AbstractPhysicalCircuit] = []

    def fake_synthesize(circuit: AbstractPhysicalCircuit, **_kwargs: object) -> Layout:
        attempts.append(circuit)
        if _contains_packed_entity(circuit):
            raise ValueError(_TWO_WIRE_COLOR_ERROR)
        return Layout(
            circuit=PhysicalCircuit("unpacked-fallback"),
            positions={},
            relays=(),
            wires=(),
            signal_allocation=(),
            net_colors=(),
        )

    monkeypatch.setattr(compiler_module, "_synthesize", fake_synthesize)
    monkeypatch.setattr(
        compiler_module,
        "layout_to_blueprint_json",
        lambda _layout: {"blueprint": {}},
    )
    monkeypatch.setattr(
        compiler_module,
        "encode_layout_blueprint_string",
        lambda _layout: "0fallback",
    )

    result = compiler_module.compile_circuit(build_worker_pool(2))

    assert len(attempts) == 2
    assert _contains_packed_entity(attempts[0])
    assert not _contains_packed_entity(attempts[1])
    assert result.abstract_physical is attempts[1]
    assert result.naive_physical is result.physical_circuit
    assert result.blueprint_string == "0fallback"
