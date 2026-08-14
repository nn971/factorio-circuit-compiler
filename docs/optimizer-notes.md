# Optimizer Notes

This file is exploratory. Hypotheses become architecture only after benchmark or in-game evidence.

## Implemented stateless baseline

- constant folding and algebraic simplification;
- common-subexpression elimination;
- dead-code elimination;
- arithmetic compatibility grouping and conservative `Each` packing;
- phase-aware scalar physical lowering;
- fresh external source offsets and automatic alignment;
- abstract state commit/phase scheduling for the current vector registers;
- semantic reference simulation for current vector state;
- tick-level physical simulation;
- reach-safe blueprint routing.

## State lesson from the working prototypes

A naïve state realization can have a much longer feedback path than a deliberately designed Factorio
circuit. Keep useful state components recognizable until a Factorio-native realization is selected.

The working `AccumulatorReg` and `FreezeReg` blueprints demonstrate whole-vector `Each` memory,
red/green network separation, continuous memory exposure, and target-specific feedback structures.

## Timing model for optimization

The logical representation distinguishes:

- source/sample identity;
- state-access order;
- physical availability.

External freshness is explicit through `Input.sample()` at a `Circuit` freshness offset. Stateless
logic is freely pipelined. State accesses carry strict v1 order identities. The state timing analysis now converts surrounding
reads into a legal semantic commit window and independently computes the physical state phase required
by operand availability. The current scope is one compound transition per trusted vector register.

## Physical synthesis priority exposed by the autonomous market

The autonomous-market controller is now large and interconnected enough to expose a gap between the
current placement objective and the concrete relay router: a placement can have a good hyperedge/MST
score yet still leave no collision-free relay chain for the router's chosen point-to-point edges.
This is a useful physical-synthesis/layout stress case, not a reason to simplify the market semantics.

Development priority is nevertheless **physical synthesis first**. Before investing in a substantially
more sophisticated placer/router, improve Factorio-native realization choices: combinator selection,
shared predicates, red/green network use, signal allocation/packing, state realization, and other
transformations that reduce or reshape the physical graph before placement.

The block corridors are intentional physical space for player access and power distribution. Ordinary
implementation combinators stay out of them. Layout-only wire relays may use the corridors, except
for a local 2x2 footprint centered at every horizontal/vertical corridor crossing. Those footprints
are reserved for substations; the remainder of each corridor stays available for walking and relay
placement. With the default 16x16 blocks and two-tile corridors, these reserved crossings repeat on
the same regular block pitch. The current compiler reserves the space but does not yet emit power
entities into it.

Placement/router improvements to revisit later include:

- making the placement objective reflect actual relay routability/congestion rather than only an
  idealized net-level relay/MST estimate;
- feeding failed routes back into subsequent placement attempts;
- routing one electrical group jointly so relay infrastructure may be shared safely inside that
  group instead of greedily allocating independent relay chains edge by edge.

## Near-term experiments

1. introduce semantic write-time anchoring for timer-like updates using the existing commit solver;
2. design a stateful timer/pulse representative test before fixing the public `at=` syntax;
3. define startup/warm-up semantics for future-sampled feedback inputs;
4. test whether explicit update handles materially improve real circuits;
5. exploit accumulator commutativity where observations cannot distinguish update order;
6. only then revisit additional state types or state packing.

Interleaving, temporal resource sharing, and processor/interpreter architectures remain postponed.
