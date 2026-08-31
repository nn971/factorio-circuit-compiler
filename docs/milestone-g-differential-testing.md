# Milestone G differential testing

Milestone G grows the compiler's differential verification surface in small typed layers. Each layer generates deterministic programs inside a supported semantic subset, compiles them through the ordinary production pipeline, and compares semantic execution with physical simulation at declared physical phases or Event materialization points.

The random generators are test inputs, not alternative semantics. The semantic simulators remain the reference behavior and the physical simulator remains the independent realization under comparison. Every randomized layer uses fixed seeds and a structure-aware reducer so a mismatch can be reproduced and minimized instead of being reported only as an opaque random seed.

## G1 — scalar combinational programs

**Status: complete.**

G1 generates small acyclic scalar DAGs over addition, subtraction, multiplication, bitwise operations, comparisons, and runtime scalar `select`. Boundary-heavy signed-32-bit inputs and arbitrary `i32` values are compiled with optimization disabled and enabled. The reducer drops outputs and bypasses scalar expression nodes.

G1 landed as commit `2065c8cef281029c6d11d5574d46ec085104504b` through PR #88.

## G2 — vector combinational programs

**Status: complete.**

G2 extends the same approach to runtime-open signal vectors. Generated programs combine sparse vector inputs with scalar controls and exercise vector arithmetic, scalar multiplication/gating, lane filters, and indexed selectors. The reducer drops outputs and bypasses vector-producing nodes while retaining nonzero physical output phases in the comparison.

G2 landed as commit `3893f6c0fd8bb2bd0656a1b989f6265c8ce4cad4` through PR #89.

## G3 — periodic state

**Status: complete.**

G3 adds stateful accumulator programs with supported logical state ordering. A period-aware stream comparator maps one semantic logical step to the inferred uniform physical state period rather than assuming one logical step equals one Factorio tick. Generated cases use sparse signed-32-bit Level traces, optimized and unoptimized compilation, and a reducer for updates, outputs, guards, and scales.

This layer deliberately covers one uniform periodic state domain. Heterogeneous periodic domains are not silently forced through the uniform-period comparator.

G3 landed as commit `756abeb8f4ec655e08c0017fd053f63806d993ed` through PR #90.

## G4 — direct Event state

**Status: complete.**

G4 adds irregular external vector Events driving Event accumulator state. Physical inputs use the compiler's explicit payload + `__valid` ABI, including present empty-vector occurrences. Each generated schedule is checked by prefix: after every Event occurrence, physical accumulator state is compared with the semantic reaction's `state_after`, not only with one final checksum.

The reducer removes Event occurrences and simplifies the per-occurrence vector transform.

G4 landed as commit `12c40afbe829f652ff92824d48599f52fa6456b5` through PR #91.

## G5 — derived clocks and explicit sampling

**Status: complete.**

G5 randomizes a public-frontend chain containing `event_merge`, `gate_clock`, `sample_on`, and explicit VALID output materialization. Semantic execution uses `simulate_events` and `materialize_output_trace`; physical execution compares payload and validity at the compiler-declared output phase for every timestamp.

Every generated case includes simultaneous parent occurrences whose scalar payloads cancel to zero while Event presence remains valid. This checks that derived clock presence is independent of payload truthiness. The reducer can remove parent occurrences, simplify output arithmetic, and clear sampled Level gate/data rows.

G5 landed as commit `7330f94503f804a1df753f41e4bc14d7b869681a` through PR #92.

## G6 — stateful Event bridges

**Status: current.**

G6 applies one shared randomized source/target Event schedule to both cross-clock vector bridges:

- `sum_into`, whose target snapshots use right-closed intervals and therefore include a simultaneous source occurrence;
- `hold_into`, whose target snapshots observe pre-state and therefore return the latest strictly prior source value when source and target occur together.

Each case also includes a target before the first source and a later target, uses sparse signed-32-bit vector payloads, and is compiled with optimization disabled and enabled. Semantic VALID materialization is compared against physical payload/valid outputs at every timestamp. The reducer removes source/target occurrences and individual vector lanes.

## Remaining Milestone G layers

After G6, the highest-value missing surface is broader output materialization (`ZERO` and `HOLD`, including default-policy behavior) and then more complex clock compositions or heterogeneous state domains where the current uniform-period stream mapping is intentionally insufficient. The generator should continue to reject unsupported shapes explicitly rather than weakening the oracle or silently treating them as covered.
