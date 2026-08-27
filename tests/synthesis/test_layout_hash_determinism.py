import json
import os
import subprocess
import sys

_CHILD = r"""
import json

from benchmarks.layout_optimizer_topology_corpus import _clustered_sparse_cut_case
from factorio_circuit.synthesis.layout_optimizer import optimize_physical_layout
from factorio_circuit.synthesis.placement import PlacementOptions

case = _clustered_sparse_cut_case()
result = optimize_physical_layout(
    case.problem,
    options=PlacementOptions(
        anchor_io=False,
        reserve_corridors=False,
        iterations=4096,
        random_seed=1,
        restarts=1,
    ),
)
fingerprint = {
    "objective": result.after.objective,
    "positions": sorted(result.layout.positions.items()),
    "wires": sorted(
        (
            wire.source_entity,
            wire.source_connector_id,
            wire.target_entity,
            wire.target_connector_id,
            wire.color.value,
        )
        for wire in result.layout.wires
    ),
}
print(json.dumps(fingerprint, sort_keys=True))
"""

_WIRE_HASH_CHILD = r"""
import json

from factorio_circuit.blueprint.routing import RoutedWire
from factorio_circuit.ir.physical import WireColor

print(
    json.dumps(
        [
            hash(RoutedWire(1, 2, 3, 4, WireColor.RED)),
            hash(RoutedWire(1, 2, 3, 4, WireColor.GREEN)),
        ]
    )
)
"""

_LEGACY_HASH_CHILD = r"""
import json

print(json.dumps([hash((1, 2, 3, 4, "red")), hash((1, 2, 3, 4, "green"))]))
"""


def _run_child(code: str, hash_seed: int) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(hash_seed)
    return subprocess.check_output(
        [sys.executable, "-c", code],
        env=env,
        text=True,
    ).strip()


def _fingerprint(hash_seed: int) -> str:
    return _run_child(_CHILD, hash_seed)


def test_seeded_layout_is_independent_of_python_hash_seed() -> None:
    fingerprints = {_fingerprint(seed) for seed in (1, 2, 3, 42)}
    assert len(fingerprints) == 1


def test_routed_wire_hash_preserves_legacy_seed_zero_order() -> None:
    legacy = tuple(json.loads(_run_child(_LEGACY_HASH_CHILD, 0)))
    hashes = {tuple(json.loads(_run_child(_WIRE_HASH_CHILD, seed))) for seed in (1, 2, 3, 42)}
    assert hashes == {legacy}
