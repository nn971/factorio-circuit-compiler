# Experimental shared exact transport

Status: experimental. This is not an accepted production compiler path.

This document condenses the durable contract from the earlier shared-delay-bus plan, design-decision, and implementation-candidate notes. The old branch-specific tuning history belongs in Git/PR history.

## Semantic boundary

Physical temporal alignment distinguishes three cases:

```text
REUSE          same chosen token is already valid later; cost 0
OBSERVE_AT     a live Level may intentionally be observed later; cost 0
EXACT_TRANSPORT
               one chosen token must be preserved to a later phase
```

Shared transport is only an implementation of `EXACT_TRANSPORT`. It must never silently turn exact token preservation into later observation of a live source.

`temporal-alignment.md` remains the authoritative detailed description of same-token reuse, late observation, and exact transport.

## Abstract lane/carrier contract

`AbstractSignal` is an abstract lane instance, not a concrete Factorio signal name and not the semantic identity of a value. Abstract lane ids remain unique; late signal coloring may assign the same concrete `SignalId` to disconnected non-conflicting lane instances.

A carrier/net may contain several abstract lanes. If lanes can coexist on one carrier, they require distinct concrete Factorio signal identities unless a stronger explicit realization proves otherwise.

The current continuous shared-bus model has no lane-retirement primitive. Once a lane enters a trunk segment, its occupancy is conservatively considered live through that segment's end.

## Isolation rule

A safe shared scalar bus uses electrical isolation at its boundaries:

```text
semantic producer lane
    -> signal-specific ingress copy
    -> fresh abstract bus lane
    -> shared Each + 0 -> Each transport stages
    -> signal-specific egress copy
    -> fresh consumer-side lane
```

The semantic producer signal is not exposed directly to an unrelated multiplexed trunk, and ordinary scalar consumers are not wired directly to a trunk carrying unrelated lanes. Coexisting trunk lanes require explicit interference/conflict information before final signal coloring.

## Planning order

Temporal analysis should eliminate free reuse/observation opportunities before transport optimization:

```text
temporal availability/alignment
    -> REUSE / OBSERVE_AT removed
    -> residual exact-transport demands
    -> private exact chain or legal shared carrier
    -> Abstract Physical IR
    -> ordinary signal coloring / red-green synthesis / layout
```

For several exact consumers of one token, form the lifetime/taps first and compare actual realizations. Do not count a phase gap as a transport demand when the consumer can legally reuse or freshly observe the value for free.

## Cost model

Price the hardware actually emitted:

- ingress/isolation copies;
- shared middle stages;
- egress/extraction copies;
- private short transports that are cheaper than joining a bus;
- any duplicated/remapped transport required by carrier constraints.

The optimizer should choose a bus only when this real cost beats the private exact-transport realization.

## Validation gate

Before promotion into production, the shared-transport primitive must demonstrate:

- tick-for-tick equivalence with private exact transport on deterministic small circuits;
- explicit producer and consumer isolation;
- safe abstract-lane conflicts and final concrete signal allocation;
- correct red/green electrical synthesis;
- a compact stateful feedback regression;
- full Snake generation and the existing in-game behavior acceptance.

Until those checks pass, `factorio_circuit.experimental` remains research code and production modules must not depend on it.
