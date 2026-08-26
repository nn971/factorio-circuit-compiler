from pathlib import Path


def replace_exact(path: str, old: str, new: str, count: int = 1) -> None:
    file = Path(path)
    text = file.read_text()
    if text.count(old) != count:
        raise RuntimeError(f"expected {count} matches in {path}")
    file.write_text(text.replace(old, new))


incremental = "src/factorio_circuit/synthesis/incremental_joint_layout.py"
replace_exact(
    incremental,
    "        for wire in affected:\n",
    "        for wire in sorted(affected, key=_routed_wire_sort_key):\n",
    count=2,
)
replace_exact(
    incremental,
    "        relay_edges = tuple(incident.get(relay_id, ()))\n",
    "        relay_edges = tuple(sorted(incident.get(relay_id, ()), key=_wire_key_sort_key))\n",
)
marker = "\ndef _remote_endpoint(\n"
helpers = '''

def _wire_key_sort_key(key: WireKey) -> tuple[int, int, int, int, str]:
    return (key[0], key[1], key[2], key[3], key[4].value)


def _routed_wire_sort_key(
    wire: wire_routing.RoutedWire,
) -> tuple[int, int, int, int, str]:
    return _wire_key_sort_key(_wire_key(wire))
'''
replace_exact(incremental, marker, helpers + marker)

observability = "src/factorio_circuit/synthesis/layout_observability.py"
replace_exact(
    observability,
    "        relay_edges = tuple(incident.get(relay_id, ()))\n",
    "        relay_edges = tuple(\n"
    "            sorted(incident.get(relay_id, ()), key=incremental._wire_key_sort_key)\n"
    "        )\n",
)

test = Path("tests/synthesis/test_layout_hash_determinism.py")
test.write_text(
    '''import os
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


def _fingerprint(hash_seed: int) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(hash_seed)
    return subprocess.check_output(
        [sys.executable, "-c", _CHILD],
        env=env,
        text=True,
    ).strip()


def test_seeded_layout_is_independent_of_python_hash_seed() -> None:
    fingerprints = {_fingerprint(seed) for seed in (1, 2, 3, 42)}
    assert len(fingerprints) == 1
'''
)
