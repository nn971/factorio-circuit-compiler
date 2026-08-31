# Milestone G3 — periodic state differential testing

G3 extends the seeded semantic-versus-physical differential harness from combinational scalar/vector programs to periodic state.

The first state layer deliberately uses one accumulator clock domain. Generated programs contain ordered `add`/`clear` transitions guarded by either scalar Level inputs or current-state lane comparisons, so state-order and inferred physical-period logic are exercised through the public frontend. Sparse vector deltas and signed-32-bit-heavy traces provide the payloads.

A periodic comparison cannot treat logical tick `n` as physical tick `n`. If the inferred state-domain period is `P`, one logical input row is held for `P` Factorio ticks and logical output `n` is checked at physical tick `n * P + output.phase`. G3 adds a dedicated comparator for this coordinate conversion rather than weakening the existing one-tick combinational comparator.

When a seeded mismatch occurs, the G3 reducer greedily removes outputs and ordered state operations, then simplifies transition guards and add scales. The reduced program remains an ordinary public-frontend circuit and is recompiled for each failure predicate.

This slice intentionally stops at one uniform periodic clock domain. Multiple periodic domains, Event state/crossings, `sample_on`, `event_merge`, `sum_into`, `hold_into`, and output materialization remain later Milestone G layers.
