"""Generate the conservative Snake temporal candidate with isolated bus ingress and egress.

This is a diagnostic wrapper around :mod:`benchmarks.snake.generate_temporal`. It keeps the same
production-ALAP schedule and CP-SAT bus plan, but swaps in the isolated temporal-plan lowerer so
shared bus wiring cannot backfeed producer nets or impose one trunk wire color directly on unrelated
semantic consumers.

The CP-SAT objective printed by the base generator remains the shared-trunk transport objective. It
does not yet price the private signal-specific ingress/egress firewall combinators added by this
physical realization probe.
"""

import sys

from benchmarks.snake import generate_temporal
from factorio_circuit.lowering.temporal_plan_isolated import (
    lower_normalized_vectors_with_isolated_temporal_plan,
)

# The base generator deliberately binds the experimental lowerer as a module global. Replacing that
# one dependency keeps all solver/provider/layout/reporting behavior identical for this A/B probe.
generate_temporal.lower_normalized_vectors_with_temporal_plan = (
    lower_normalized_vectors_with_isolated_temporal_plan
)


if __name__ == "__main__":
    print(
        "isolated temporal bus realization: signal-specific ingress + shared trunk + "
        "signal-specific egress; solver objective excludes firewall copies",
        file=sys.stderr,
    )
    generate_temporal.main()
