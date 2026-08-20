# External-device anchor ABI

External devices and compiled controller fragments are composed through **named exact-overlap
anchors**, not by searching for internal entities and adding wires after generation.

## Motivation

A blueprint can have valid entity IDs and legal wire lengths while still containing an electrically
dead inter-component network.  The failed autonomous-mall adapter demonstrated this: a manually added
wire reached a valid combinator, but the network feeding that combinator had no producer.

The anchor ABI makes the component boundary explicit and validates it before composition.

## Physical rule

An anchor is a constant-combinator terminal with typed metadata:

```text
name
direction        INPUT | OUTPUT
payload shape    SCALAR | VECTOR
temporal mode    LEVEL | EVENT
wire color       RED | GREEN
fixed signal     required for scalar anchors
position
connector
```

Two components connect by placing compatible anchors at the same position and **merging the two
terminal entities into one**:

```text
component A internal circuit --> [ANCHOR]
                                  [ANCHOR] --> component B internal circuit

                         becomes

component A internal circuit --> [SHARED ANCHOR] --> component B internal circuit
```

The composer adds no cross-component circuit wire.

## Validation

Before a component can participate in composition, every declared anchor must refer to a real
constant-combinator terminal and must be locally connected on the declared wire/connector.  An OUTPUT
anchor may instead be a non-empty constant-combinator source.

A binding is rejected unless:

- one side is OUTPUT and the other INPUT;
- payload shapes match;
- temporal modalities match;
- fixed scalar signals match;
- wire colors match;
- translated anchor positions overlap exactly;
- both sides are electrically live before merging;
- all final wire references remain valid.

This intentionally checks a stronger property than “all wire entity IDs exist”.

## External-device wire-color convention

The current device protocol uses the following convention:

```text
GREEN = commands entering an external device
RED   = observations leaving an external device
```

This remains a design principle rather than a universal compiler rule.  The anchoring metadata makes
it possible to enforce or relax the convention per protocol later without relying on entity layout.

## AssemblerDevice

`AssemblerDevice` exposes its existing ports as first-class anchors:

```text
GREEN inputs
    recipe
    enable
    requester_demand

RED outputs
    ingredients
    requester_contents
    provider_contents
    working
    finished
```

Its requester/assembler/provider internals remain opaque to callers.

## Mall integration direction

The previous hand-wired `AssemblerDevice` mall adapter is abandoned.  The next worker should be a new
compiled controller whose device-facing inputs/outputs are materialized directly as anchors matching
`AssemblerDevice`.  The only post-compilation operation should be exact-overlap anchor composition.

Before reconnecting the worker FSM, validate the anchor mechanism with the standalone assembler anchor
probe: a constant source is merged with the `recipe` and `requester_demand` anchors, while a lamp
observer is merged with the `ingredients` anchor.  No bridge wire is synthesized between components.
