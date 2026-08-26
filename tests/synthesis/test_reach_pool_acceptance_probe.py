"""Temporary one-shot acceptance probe for reach-mobile proposal filtering.

Delete this file before merging. It intentionally fails after printing the paired benchmark output so
GitHub Actions preserves the complete comparison in the ordinary pytest job log.
"""

from __future__ import annotations

import subprocess
import sys


def test_reach_pool_acceptance_probe() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.layout_optimizer_reach_pool_compare",
            "--proposals",
            "4096",
            "--seeds",
            "8",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    raise AssertionError(
        "ONE-SHOT REACH-MOBILE ACCEPTANCE\n"
        f"returncode={result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
