from __future__ import annotations

from pathlib import Path
import subprocess
import sys

joint = Path("src/factorio_circuit/synthesis/incremental_joint_layout.py")
if "_FILTER_REACH_IMMOBILE_PROPOSALS = True" not in joint.read_text():
    raise SystemExit("reach-mobile experiment is not present on the checked-out branch")

subprocess.run(
    [sys.executable, "tools/_optimize_reach_mobile_pool_experiment.py"],
    check=True,
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "benchmarks.layout_optimizer_reach_pool_compare",
        "--proposals",
        "4096",
        "--seeds",
        "8",
    ],
    check=True,
)
