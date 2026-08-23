from collections import deque

import pytest

from examples.autonomous_mall.worker_pool import build_worker_pool
from factorio_circuit import lower_to_abstract_physical
from factorio_circuit.synthesis.open_vector import VectorPhysicalSynthesizer


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


@pytest.mark.parametrize("worker_count", [1, 2])
def test_worker_pool_net_constraints_are_two_colorable(worker_count: int) -> None:
    lowered = lower_to_abstract_physical(build_worker_pool(worker_count))
    synthesizer = VectorPhysicalSynthesizer(lowered.abstract_physical)
    cycle = _odd_cycle_labels(synthesizer)
    assert not cycle, "odd wire-color conflict cycle: " + " -> ".join(cycle)
