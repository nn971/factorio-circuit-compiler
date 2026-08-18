# Level settling and phase-padding elimination

The Level backend treats clock period as a **settling budget**, not as a path length that every value
must physically traverse.

## Feedback-cut theorem

Consider one logical state domain with period `P`.  Cut the semantic state elements and view every
state read as a source.  The remaining expression graph is feed-forward.  If a source represents the
same logical token on a physical interval `[a,b)`, then a combinational operation is guaranteed to
represent its logical result on the intersection of its operand-validity intervals, shifted by the
operation latency.

In particular, a state read at physical phase `phi` represents one state token throughout

```text
[phi, phi + P)
```

because the physical state element holds that token until the next clock boundary.  Therefore two
paths do not need equal arrival phases.  If an early operand is still the same token when the later
operand becomes ready, the consumer can use both directly.

For a synchronous recurrence

```text
S[k+1] = F(S[k], I[k])
```

whose state-transition inputs settle before the next boundary, all identity delays inserted solely
to pad state-derived paths to one exact phase are redundant.  Removing such a delay cannot change
`F`; it only lets an already-correct Level remain on its original net.

The theorem does **not** say that every delay is removable.  A fresh external Level sample may change
on the next physical tick, so unequal paths from that sample can denote different logical
occurrences unless the short path is delayed or the input is explicitly captured.  Intentional
startup delays and Event/pulse transport also carry temporal meaning and are not phase padding.

## Production lowering rule

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

## Output observation

This optimization proves correctness at semantic observation/capture phases.  A continuously visible
physical output can still show normal combinational settling transients between clock activations.
The Clocked Flow design already reserves output materialization (`HOLD`, `ZERO`, `VALID`) as the layer
that defines dense between-activation behavior.  Output materialization is separate from internal
phase-padding elimination.
