# Factorio 2.x circuit-mechanics invariants

This file records target-game facts that materially affect compiler and circuit architecture.
They are project invariants: read them before estimating physical size or designing a ROM.

## Constant combinators are whole-vector sources

**Do not model a Factorio 2.x constant combinator as a 20-signal storage device.**

A current Factorio 2.x constant combinator can be configured to emit a whole signal vector, with
far more than 20 configured values from one entity. The old "20 filters / 20 values per constant
combinator" assumption comes from legacy Factorio behavior/documentation and is not a valid basis
for this compiler's Factorio 2.x target.

Consequences:

- Never estimate constant-combinator count as `ceil(number_of_values / 20)`.
- Never reject or redesign a vector ROM merely because it has more than 20 signal entries.
- Treat one constant combinator as a vector-valued source at the architectural level.
- If an exact current-game upper bound on configured entries is important for a particular
  implementation, establish it from the current Factorio 2.x game/blueprint format or an in-game
  probe. Do not substitute a legacy wiki limit.
- Storage-complexity discussions must distinguish **number of configured signal values** from
  **number of constant-combinator entities**. A ROM whose vector width grows with supported items
  can still use a constant number of constant-combinator entities.

This is especially important for item-keyed ROMs and associative lookup designs: signal identity
provides the key space, while one constant combinator can broadcast the corresponding large vector.

## Maintenance rule

When a target-game mechanic conflicts with remembered Factorio 1.x behavior or an old wiki entry,
prefer a current Factorio 2.x in-game probe / current blueprint schema and update this file with the
result. Do not carry legacy hardware limits forward by assumption.
