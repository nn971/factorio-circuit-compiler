# External-device anchor ABI

External devices and generated controller fragments can be composed through **named exact-overlap anchors** instead of searching implementation entities and adding ad-hoc bridge wires after generation.

## Physical model

An anchor is a typed constant-combinator terminal. Its contract records:

- stable name;
- direction (`INPUT` or `OUTPUT`);
- payload shape (`SCALAR` or `VECTOR`);
- temporal modality (`LEVEL` or `EVENT`);
- wire color;
- fixed signal for scalar anchors;
- concrete entity, connector, and position.

Two independently generated components connect by translating compatible anchors to the same coordinate and merging the two terminal entities into one shared junction. The composer does not invent a new cross-component circuit wire.

```text
component A internal circuit --> [ANCHOR]
                                  [ANCHOR] --> component B internal circuit

                         becomes

component A internal circuit --> [SHARED ANCHOR] --> component B internal circuit
```

## Validation

`AnchoredBlueprint` validates each declared terminal before composition. A binding is rejected unless:

- the anchors have opposite directions;
- payload shapes and temporal modalities match;
- wire colors match;
- fixed scalar signals match;
- translated anchor positions overlap exactly;
- each anchor refers to the declared constant-combinator connector;
- each side is electrically live before composition;
- all resulting wire references remain valid.

An output anchor may be a non-empty constant-combinator source without an incident internal wire. Otherwise an anchor must already be connected to its own component internals.

## Device adaptation

`ExternalDeviceBlueprint.anchored()` exposes any typed external device through the same anchor API. This keeps mechanical device generation separate from application control policy while giving compiled or hand-generated controllers a uniform physical composition boundary.

The current reusable-device convention is:

```text
GREEN = commands entering a device
RED   = observations leaving a device
```

This is a protocol convention, not a compiler-wide restriction; individual `AnchorSpec` values remain authoritative.

## Compiled-module adaptation

`compiled_module_as_anchored_blueprint(...)` adapts named compiler ports to stable anchor contracts. It inserts only boundary isolation/renaming infrastructure and never discovers ports by description or reaches into application-specific internals.

The adapter position is collision-checked against the already generated module. If the preferred arithmetic-combinator footprint is occupied, the wrapper searches a deterministic nearby legal position while preserving the configured maximum wire hop. Failure to find a legal adapter is reported instead of emitting an unplaceable blueprint.

For reusable constrained components, prefer pinning final named compiler ports with `compile_circuit(..., port_positions=...)` before placement so the physical implementation is synthesized around its real public boundary.

## Scope

The anchor ABI owns electrical boundary compatibility and exact-overlap composition. Bounded component geometry and ordered multi-lane interfaces are provided separately by `component-seam-abi.md`. Application scheduling, inventory accounting, and domain-specific protocols belong above both layers.
