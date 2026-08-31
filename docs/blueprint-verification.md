# Independent blueprint verification

Milestone I verifies the serialized Factorio artifact independently of physical synthesis. The verifier is intentionally downstream of placement, routing, opaque-device composition, and serialization: compiler layouts may be used to *produce test fixtures*, but verifier decisions must not consult synthesis `Layout`, routed-net assignments, placement occupancy, or other mutable construction state.

**Milestone I status: complete.**

## I1 — serialized structural verifier

**Status: complete.**

`factorio_circuit.blueprint.verify` accepts any of:

- the outer `{"blueprint": ...}` JSON wrapper emitted by the compiler;
- the inner ordinary-blueprint object;
- a Factorio import string, decoded independently from base64/zlib/JSON.

Verification also receives an explicit prototype catalogue. Each `BlueprintPrototypeSpec` declares only the physical facts needed by I1:

- collision half-extents in the prototype's direction-0 orientation;
- whether those half-extents rotate with Factorio cardinal `direction`;
- legal Factorio circuit connector ids;
- conservative maximum centre-to-centre wire span.

`compiler_prototype_specs()` provides the small explicit catalogue for compiler-native constant, arithmetic, decider, and selector combinators. Opaque, modded, or reusable-device prototypes are not guessed: callers extend the catalogue explicitly. This preserves the same no-hidden-prototype-database principle used by imported devices while keeping the verifier independent from synthesis.

For arithmetic/decider/selector combinators the canonical direction-0 selection footprint is 1x2, represented by half-extent `(0.5, 1.0)`. The compiler serializes its ordinary horizontal combinators with `direction: 4`, so I1 rotates that canonical footprint to the 2x1 horizontal geometry used by placement. I4 exposed and corrected an earlier reversed canonical declaration.

### I1 checks

The verifier rejects serialized artifacts containing:

- malformed, non-positive, or duplicate entity numbers;
- missing prototype names or prototypes absent from the supplied catalogue;
- missing, non-numeric, or non-finite entity positions;
- overlapping declared collision footprints after applying serialized cardinal orientation;
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

## I3 — serialized ABI and rigid geometry

**Status: complete.**

`factorio_circuit.blueprint.geometry_verify` invokes I1 and then checks only explicit post-serialization geometry expectations. It imports no synthesis `Layout`, `RigidComponentConstraint`, anchor-binding object, or seam-composition object.

I3 separates geometry contracts that have different semantics:

1. **Exact ABI anchors.** `BlueprintAnchorExpectation` names one serialized entity/connector, exact entity-centre position, and optional prototype. A moved endpoint, wrong connector, wrong entity id, or wrong prototype rejects.
2. **Boundary seams.** `BlueprintSeamExpectation` groups exact anchors on a declared boundary rectangle. This matches reusable seam composition, where boundary anchor centres may sit exactly on the footprint boundary and therefore are not treated as D1 owned-region members.
3. **Rigid components.** `BlueprintRigidComponentExpectation` declares an origin, quarter-turn pose, member-local offsets, owned footprints, keepouts, and adapter regions. Members must remain at their rigid offsets and fit inside owned footprints; outsiders may not enter owned/keepout geometry; adapter regions must remain empty even of component members.

All collision-region checks use the orientation-aware serialized half-extent from the I1 prototype catalogue.

### I3 mutation coverage

The routine I3 regressions exercise:

- moved serialized ABI anchors;
- invalid anchor connectors and prototype mismatches;
- seam-anchor drift;
- rigid-member drift;
- external incursions into owned and keepout geometry;
- occupation of reserved adapter regions;
- duplicate rigid-component member ownership;
- quarter-turn reconstruction without synthesis state;
- rotated native wide-combinator footprint overlap and legal boundary touching.

## I4 — opaque-provider end-to-end acceptance

**Status: complete.**

I4 exercises I1-I3 against real reusable-device/provider artifacts using verifier-side expectations rather than the synthesis state being audited.

### Routine opaque-device contract

`tests/blueprint/test_opaque_device_verifier_contract.py` verifies the standalone 25-entity `AssemblerDevice` blueprint directly. Its contract is a static description of the serialized device ABI:

- all 25 member ids, prototypes, and fixed positions;
- the owned footprint, keepout, and adapter regions used by the provider integration;
- all 23 root-level wires;
- 13 intended physical electrical components, including isolated recipe/enable/requester commands, the merged machine-command network, raw assembler status fanout, sanitized ingredient merge, working/finished outputs, and requester/provider observation lanes.

The test passes the device blueprint itself to I1-I3. It does not reconstruct expectations from a synthesis `Layout`, `RigidComponentConstraint`, or routed-net table.

### Full mixed-provider acceptance

`tests/integration/test_i4_e3_serialized_verification.py` compiles the existing E3 mixed-provider benchmark containing ordinary logic, a free provider, a world-anchored provider, and the real opaque assembler device. After compilation, the verification path consumes only `result.blueprint_string`.

Static verifier-side facts identify the assembler members from their serialized stable descriptions and assert:

- I1 structural validity for the exact import string;
- complete public marker names/directions for `x`, `recipe`, `logic`, and `ingredients`;
- serialized GREEN electrical equivalence between the public recipe marker and assembler recipe dock;
- serialized RED electrical equivalence between the assembler ingredient dock and public ingredients marker;
- exact position/prototype of the anchored world sensor;
- all 25 opaque assembler member offsets and its owned/keepout/adapter geometry.

The test deliberately does not read `CompilationResult.layout`, `abstract_physical` net ids, assigned net colours, or `physical_circuit` entity/net state to establish those expectations. It passed once in ordinary CI as the I4 acceptance gate and is retained behind `@pytest.mark.acceptance` so routine feedback remains lean.

### I4 orientation correction

The first I4 run usefully rejected both the standalone assembler and mixed E3 artifact because I1's native wide-combinator catalogue had the direction-0 half-extent reversed. Checking the serialized direction against Factorio's native prototype orientation showed that the compiler/serializer geometry was internally consistent and the independent verifier was wrong. Correcting the canonical direction-0 footprint made both artifacts pass without weakening geometry checks or changing synthesis.

## Milestone I closure

I1-I4 now cover the intended independent post-serialization contract:

- local entity/connector/wire structural validity;
- red/green electrical reconstruction and separation;
- public serialized ports;
- exact ABI anchors and seams;
- rigid member, footprint, keepout, and adapter geometry;
- intended electrical equivalence across a real opaque provider boundary;
- a full mixed compiler/provider import string checked without reusing final synthesis state.

That is sufficient to close Milestone I. Ordinary blueprint books and other container-level formats remain outside the verifier's current input contract; if those formats become compiler outputs that need independent validation, they should be handled as a separate follow-on format/container milestone rather than keeping this physical-artifact milestone open.
