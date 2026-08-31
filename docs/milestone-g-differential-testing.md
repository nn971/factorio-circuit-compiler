# Milestone G differential testing

**Status: complete.**

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

**Status: complete.**

G6 applies one shared randomized source/target Event schedule to both cross-clock vector bridges:

- `sum_into`, whose target snapshots use right-closed intervals and therefore include a simultaneous source occurrence;
- `hold_into`, whose target snapshots observe pre-state and therefore return the latest strictly prior source value when source and target occur together.

Each case also includes a target before the first source and a later target, uses sparse signed-32-bit vector payloads, and is compiled with optimization disabled and enabled. Semantic VALID materialization is compared against physical payload/valid outputs at every timestamp. The reducer removes source/target occurrences and individual vector lanes.

G6 landed as commit `7bbd65c1a47bcf86d4274f2dd870386aedefd344` through PR #93.

## G7 — output materialization

**Status: complete.**

G7 randomizes irregular scalar Event outputs under explicit `ZERO` and `HOLD` policies. Generated traces include a present zero-valued occurrence so Event presence cannot be confused with payload truthiness. Semantic `materialize_output_trace` results are compared with the single physical payload output at the compiler-declared phase for every timestamp, with optimization disabled and enabled.

The same slice checks that additive `sum_into` outputs default to `ZERO` materialization when no explicit policy is supplied. The reducer removes Event occurrences and simplifies the affine output transform.

G7 landed as commit `12f273f351c58609d16f7d32e396154ecb369e0f` through PR #94.

## G8 — acceptance closure and clock-shape filtering

**Status: complete.**

G8 makes the fuzz-harness boundary executable rather than implicit. The uniform-period periodic oracle classifies compiler timing before comparison:

- a compiler-supported independent period-1 / period-3 state-domain program has `uniform_period=None` and is explicitly filtered with a deterministic reason;
- connecting those domains through one same-index expression unifies them to period 3 and makes the shape eligible for the uniform-period oracle;
- Event-clock timing is routed to the Event differential harness rather than being misinterpreted as periodic timing.

G8 also adds a clock-structure reducer that can remove derived-clock stages while preserving a failure predicate, and acceptance checks that representative crossing, Event-bridge, and output-materialization generators reproduce identical cases for identical seeds.

This is intentionally a harness classification boundary, not a claim that heterogeneous periodic domains are compiler errors. A future per-domain logical-to-physical comparator can broaden the fuzz surface without changing the current acceptance result.

## Milestone G acceptance

The roadmap acceptance requirements are satisfied:

- seeded random cases are deterministic and reproducible;
- expression reducers cover scalar/vector DAGs, state reducers cover periodic/Event state and Event bridges, and the G8 reducer covers clock topology;
- supported scalar/vector arithmetic, periodic state, Event state, `sample_on`, `gate_clock`, `event_merge`, `sum_into`, `hold_into`, and VALID/ZERO/HOLD output materialization all have semantic-vs-physical differential coverage;
- shapes that do not fit a specific oracle are classified explicitly instead of silently compared under an invalid timing assumption.

Milestone G is therefore complete. Further expansion of the random language or a per-domain heterogeneous-period comparator is future verification work, not a prerequisite for the accepted differential-testing baseline.
