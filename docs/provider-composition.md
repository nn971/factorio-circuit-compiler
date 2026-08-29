# Unified rigid-provider composition

Milestone E2 makes `ProviderRigidComponentProduct` a real full-compilation input rather than an
inspection-only declaration. A Level oracle provider can now bind a reusable
`ExternalDeviceBlueprint` directly into the compiler's final physical artifact before routing.

This document describes the correctness boundary of that composition path. It is deliberately
separate from device generation: a device still owns its protocol and internal blueprint, while E2
owns how that already-defined physical component joins compiler-generated logic.

## Inputs to composition

The composer receives:

- the ordinary `AbstractPhysicalCircuit` after oracle-provider materialization;
- one or more validated `ProviderRigidComponentProduct` values;
- the normal placement options and symbolic physical-anchor mapping;
- the compiler's conservative external `safe_wire_span`.

Each rigid product already carries the reusable blueprint, explicit per-prototype collision geometry,
D1 footprint/keepout/adapter regions, optional access points/legal origins, an internal wire envelope,
and typed device-port-to-abstract-net bindings.

No hidden Factorio prototype database is introduced. As in D4, the reusable component must supply the
physical facts needed to import its entities.

## Composition sequence

Full compilation performs the following steps before the final blueprint is serialized:

1. Import every reusable blueprint through the D4 opaque importer and rebase component-local entity
   ids into compiler-global ids.
2. Preserve the imported entities' exact opaque blueprint payloads and declared physical half-extents.
3. Apply every typed port binding to its existing abstract net. The device port fixes the required
   red/green wire color; a scalar port may additionally fix the concrete `SignalId` for that abstract
   lane.
4. Feed fixed scalar identities through the ordinary DSATUR interference allocator as precolored
   abstract lanes. Conflicting alias/interference assignments reject instead of bypassing the normal
   signal-safety checks.
5. Create one temporary annotation proxy for each bound device port. The proxy is anchored at a
   conservative connector-side point near the eventual opaque endpoint so the existing placer and
   electrical synthesizer can reason about the connection before opaque entities are inserted.
6. Place the ordinary implementation with those temporary proxy anchors present, then deterministically
   legalize ordinary entities away from every component footprint, keepout, and reserved adapter
   region.
7. Discard the proxy route and fresh-route the combined ordinary logic while component-owned regions
   are forbidden to relay placement. The constructive routing span is reduced by the worst
   proxy-to-real-endpoint displacement, so replacing a proxy cannot silently create an over-span
   external wire.
8. Replace every proxy endpoint by the exact opaque entity id and Factorio connector id, append only
   the component's already-imported internal wires, and materialize one final `Layout`.
9. Validate the final mixed artifact through the D1 component-geometry boundary, validate external
   wire spans against the compiler span, and validate imported internal wires against each component's
   declared internal envelope.
10. Serialize the mixed opaque/non-opaque layout through the opaque-aware blueprint encoder.

The temporary proxies are therefore construction scaffolding only. They do not survive in the final
`PhysicalCircuit`, `Layout`, or blueprint.

## Electrical invariants

A reusable device port participates in the same physical allocation rules as compiler logic:

- required red/green colors must be consistent with the abstract net-conflict graph;
- scalar fixed signals are precolored in the shared DSATUR allocator, not assigned by a separate E2
  allocator;
- two interfering abstract lanes cannot be forced to the same concrete signal;
- vector ports remain runtime-open vectors and do not reserve one scalar signal identity;
- the exact opaque connector id is restored before final validation and serialization.

A contradiction is a compilation error. E2 does not silently recolor the device ABI or weaken signal
interference constraints to make a composition fit.

## Geometry and routing invariants

Opaque device entities use their imported `physical_half_extent`; they are not treated as generic
1x1 or 2x1 combinators. Component footprints, keepouts, and adapter regions are authoritative hard
geometry for ordinary entities and relay workspace.

Device-internal wires are not rerouted as if they were compiler-generated connections. They are
preserved from the imported component and checked against the product's `internal_wire_span`. All
new external wires use the compiler's ordinary conservative span.

Final validation is performed after proxy substitution, so the artifact being checked is the same
mixed physical object that is encoded into the blueprint.

## Current limitations

E2 is intentionally narrower than a general physical-module optimizer:

- physical oracle providers remain Level-only; Event oracle providers are still unsupported;
- rigid provider components are composed at their declared geometry. Full `compile()` does not yet
  invoke D2's automatic finite-origin translation search for them, and it does not rotate them;
- reserved adapter regions remain empty hard geometry; E2 does not synthesize protocol adapters into
  those regions;
- the reusable blueprint must be representable by the current opaque single-/dual-connector importer
  with explicit prototype geometry;
- ordinary-implementation legalization around component geometry is deterministic and bounded; an
  impossible fixed-anchor/component overlap rejects rather than moving the fixed object.

These limitations are optimization/coverage boundaries, not holes in the serialized correctness
contract. A successful E2 compilation has already passed exact mixed-layout validation.
