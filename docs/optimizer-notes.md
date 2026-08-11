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

## Near-term experiments

1. introduce semantic write-time anchoring for timer-like updates using the existing commit solver;
2. design a stateful timer/pulse representative test before fixing the public `at=` syntax;
3. define startup/warm-up semantics for future-sampled feedback inputs;
4. test whether explicit update handles materially improve real circuits;
5. exploit accumulator commutativity where observations cannot distinguish update order;
6. only then revisit additional state types or state packing.

Interleaving, temporal resource sharing, and processor/interpreter architectures remain postponed.
