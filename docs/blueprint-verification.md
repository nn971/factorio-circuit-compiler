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

## Remaining Milestone I work

I1 establishes local serialized structural validity. Later slices should build on the independently parsed entity/connector graph to verify broader contracts without falling back to synthesis state:

1. reconstruct red and green connected components and check declared public/device endpoint connectivity;
2. verify ABI anchors, seams, rigid-component regions, and other externally declared geometry from explicit expectations;
3. compare intended electrical-net equivalence against independently reconstructed serialized networks where practical.

Those checks require explicit post-serialization expectations; they should not be inferred by reaching back into the final `Layout`.
