"""Generate the conservative Snake temporal candidate with electrically isolated bus ingress.

This is a diagnostic wrapper around :mod:`benchmarks.snake.generate_temporal`. It keeps the same
production-ALAP schedule and CP-SAT bus plan, but swaps in the isolated temporal-plan lowerer so
shared bus wiring cannot backfeed unrelated scalar lanes onto original producer nets.
"""

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
    generate_temporal.main()
