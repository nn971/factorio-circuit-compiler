# Level settling, ALAP scheduling, and phase-padding elimination

The Level backend treats clock period as a **settling budget**, not as a path length that every value
must physically traverse.

## Feedback-cut theorem

Consider one logical state domain with period `P`. Cut the semantic state elements and view every
state read as a source. The remaining expression graph is feed-forward. If a source represents the
same logical token on a physical interval `[a,b)`, then a combinational operation is guaranteed to
represent its logical result on the intersection of its operand-validity intervals, shifted by the
operation latency.

In particular, a state read at physical phase `phi` represents one state token throughout

```text
[phi, phi + P)
```

because the physical state element holds that token until the next clock boundary. Therefore two
paths do not need equal arrival phases. If an early operand is still the same token when the later
operand becomes ready, the consumer can use both directly.

For a synchronous recurrence

```text
S[k+1] = F(S[k], I[k])
```

whose state-transition inputs settle before the next boundary, all identity delays inserted solely
to pad state-derived paths to one exact phase are redundant. Removing such a delay cannot change
`F`; it only lets an already-correct Level remain on its original net.

The theorem does **not** say that every delay is removable. A fresh external Level sample may change
on the next physical tick, so unequal paths from that sample can denote different logical
occurrences unless the short path is delayed or the input is explicitly captured. Intentional
startup delays and Event/pulse transport also carry temporal meaning and are not phase padding.

## Backward ALAP scheduling

Validity reuse alone is insufficient for an external snapshot fanout. ASAP lowering can create

```text
x -> f -> delay delay delay ...
x -> g -> delay delay delay ...
x -> h -> delay delay delay ...
```

because each cheap branch is computed immediately and only its distinct result is later transported
to the state boundary. By then the branches no longer share a physical net, so a delay-prefix cache
cannot merge their transport.

`factorio_circuit.lowering.alap.AlapVectorLowerer` therefore computes an as-late-as-possible schedule
for periodic state-transition cones. Every state value input is demanded at its inferred
`transition_input_phase`; scalar controls are demanded one final state-control stage earlier. These
deadlines propagate backwards through the semantic DAG by target latency. A shared node receives the
earliest deadline of all its consumers.

For a one-stage operation whose result is required at phase `T`, the preferred input phase is
`T - 1`. Lowering never moves an operation earlier than its ordinary ASAP phase, so missing or
inapplicable ALAP information falls back naturally to the previous schedule.

Crucially, ALAP moves **computation**, not the meaning of an external snapshot. Raw scalar/vector
Level samples retain their existing physical sample phase. If a snapshot is needed later, exact
transport is pushed toward that shared leaf. Scalar and vector delay-prefix caches can then share the
transport before cheap computations branch:

```text
x -> shared exact-delay trunk ->+-> f -> state boundary
                                +-> g -> state boundary
                                +-> h -> state boundary
```

For held state and constants even that trunk is normally free because their validity windows already
cover the scheduled phase.

The first production ALAP pass intentionally targets periodic state cones. Ordinary output-only
expressions retain their previous schedule, and packed scalar implementations may retain their
packing-selected phase when packing is chosen. These are optimization limitations, not semantic
exceptions.

Scalar `Select` uses the conservative generic three-stage data-path envelope while scheduling. If the
physical lowerer later realizes the select as a shorter decider mux, its result may simply become
available before the deadline and ordinary validity/delay rules handle the remaining slack.

## Production validity rule

`factorio_circuit.lowering.settling.SettlingVectorLowerer` carries a certified half-open validity
window for realized Level values:

- constants: unbounded;
- state reads: one complete state period;
- raw scalar/vector Level samples: one physical tick;
- ordinary operations: intersection of aligned operand windows, shifted to the result phase.

When lowering requests `delay_to(value, target_phase)`:

1. if `target_phase` lies inside the certified validity window, the same physical net is reused and
   no combinator is emitted;
2. otherwise the backend falls back to the previous exact delay chain.

The fallback makes the optimization conservative: missing or insufficient proof never removes
hardware.

The periodic clock's startup-ready chain is explicitly forced through the exact path because it
suppresses premature modulo-clock residues; a constant being numerically stable does not make that
temporal guard redundant.

When exact vector transport is necessary, `SharedVectorDelayLowerer` memoizes every one-tick vector
prefix just like the scalar delay cache. Multiple consumers of the same physical vector therefore
share one trunk. ALAP is what exposes this sharing in the common `f(x), g(x), ...` external-snapshot
pattern by moving the fanout computations toward their consumers.

## Output observation

Level outputs default to the semantic `HOLD` materialization policy. The lowerer first asks whether
the realized output already remains the same logical token for a complete occurrence interval. If so,
HOLD is free and the original net is exported directly.

If the certified output window is shorter than its clock period, the lowerer inserts a compact
periodic boundary cell:

```text
coherent payload ---- capture when clock phase matches ----+
                                                        memory ---- output
memory -------- retain between logical boundaries --------+
```

The capture and feedback branches are two decider combinators. A shared periodic clock and startup
ready chain ensure that the cell does not capture an early settling transient before logical step zero
is available. The held output is then stable for the whole interval until the next occurrence.

This keeps the distinction explicit:

- internal combinational settling is free when validity proves it safe;
- computation is placed ALAP inside periodic state cones so exact transport stays upstream and can be
  shared;
- external dense observation is synchronized only at the semantic output boundary.

The current production Level path implements the default HOLD behavior. ZERO/VALID remain primarily
Event-boundary policies; adding explicit periodic-Level ZERO/VALID lowering is a separate extension.
