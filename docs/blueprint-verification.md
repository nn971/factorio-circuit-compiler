# Independent blueprint verification

Milestone I verifies the serialized Factorio artifact independently of physical synthesis. The verifier is intentionally downstream of placement, routing, opaque-device composition, and serialization: compiler layouts may be used to *produce test fixtures*, but verifier decisions must not consult synthesis `Layout`, routed-net assignments, placement occupancy, or other mutable construction state.

## I1 — serialized structural verifier

**Status: complete.**

`factorio_circuit.blueprint.verify` accepts any of:

- the outer `{"blueprint": ...}` JSON wrapper emitted by the compiler;
- the inner ordinary-blueprint object;
- a Factorio import string, decoded independently from base64/zlib/JSON.

Verification also receives an explicit prototype catalogue. Each `BlueprintPrototypeSpec` declares only the physical facts needed by I1:

- collision half-extents;
- legal Factorio circuit connector ids;
- conservative maximum centre-to-centre wire span.

`compiler_prototype_specs()` provides the small explicit catalogue for compiler-native constant, arithmetic, decider, and selector combinators. Opaque, modded, or reusable-device prototypes are not guessed: callers extend the catalogue explicitly. This preserves the same no-hidden-prototype-database principle used by imported devices while keeping the verifier independent from synthesis.

### I1 checks

The verifier rejects serialized artifacts containing:

- malformed, non-positive, or duplicate entity numbers;
- missing prototype names or prototypes absent from the supplied catalogue;
- missing, non-numeric, or non-finite entity positions;
- overlapping declared collision footprints;
- malformed root-level wire tuples;
- wires referring to absent entity numbers;
- connector ids not exposed by the endpoint prototype;
- red/green connector-id mismatches within one wire;
- centre-to-centre wire spans longer than the stricter endpoint declaration.

Touching collision boxes are accepted. For wire reach, each endpoint may declare a different conservative span; the verifier uses the minimum. This lets a reusable device retain a larger legitimate internal reach without weakening an edge whose other endpoint uses the compiler's conservative 7-tile construction limit.

On success, `BlueprintVerificationReport` records entity count, wire count, and the set of serialized prototypes observed.

### Independence regression strategy

The ordinary tests include one compiler-produced blueprint as a fixture and then mutate standalone serialized objects to exercise failure classes. The verifier itself imports neither synthesis `Layout` nor routing validation helpers. Consequently, a serializer bug that produces duplicate ids, wrong connector ids, overlap, or over-reach wiring cannot be hidden by reusing the object graph that created the artifact.

## I2 — serialized electrical connectivity and public ports

**Status: complete.**

`factorio_circuit.blueprint.connectivity_verify` first invokes I1 and then independently rebuilds the serialized electrical graph from root-level Factorio wire tuples. A graph node is an exact `(entity_number, connector_id)` pair. Red and green remain separate because Factorio connector-id parity is part of the serialized endpoint identity; the verifier does not import synthesis net colours or net-group assignments.

I2 adds three post-serialization contracts:

1. **Physical electrical equivalence.** `BlueprintNetExpectation` lists endpoints that must belong to one reconstructed connected component.
2. **Electrical separation.** Distinct expected physical nets must not reconstruct to the same component. Expectations therefore describe already-coalesced *physical* groups, not abstract logical nets that synthesis is explicitly allowed to share safely.
3. **Public marker identity/connectivity.** Compiler marker annotations are parsed from the serialized `player_description` form `[FCC #N | marker] INPUT/OUTPUT ...`. The embedded id must match the serialized entity number, public names/directions must match the complete supplied contract when one is provided, a public marker may use at most one wire colour, and optional declared peer endpoints must be reachable on that reconstructed component.

`BlueprintConnectivityReport` records the number of wire-bearing reconstructed components, the verified physical-net names, and every serialized public port discovered from the artifact.

### I2 mutation coverage

Routine regressions deliberately mutate serialized fixtures to detect:

- a missing wire that breaks an expected physical net;
- an extra wire that shorts two expected physical nets;
- contradictory expectations that assign one endpoint to multiple intended physical groups;
- a renamed/extra public marker;
- a public marker disconnected from its declared peer;
- a public marker connected to both red and green networks;
- a marker annotation whose embedded FCC entity id disagrees with the serialized entity number.

A real compiler-produced import string is also checked for public input/output discovery without consulting `CompilationResult.layout` or `PhysicalCircuit.inputs/outputs` during verification.

## Remaining Milestone I work

I1 and I2 establish local structural validity plus independent serialized electrical reconstruction. The next slices should keep using explicit post-serialization expectations rather than reaching back into synthesis state:

1. verify ABI anchors and exact public/device endpoint positions;
2. verify seams, owned/keepout/adapter regions, and rigid-component membership from explicit geometry contracts;
3. extend electrical-equivalence expectations across richer opaque-provider/device acceptance fixtures where useful.

These geometry contracts should be supplied independently by the caller or benchmark fixture. The verifier should never recover them by reading the final synthesis `Layout` it is supposed to audit.
