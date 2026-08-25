# Factorio 2.x circuit-mechanics invariants

This file records target-game facts that materially affect compiler and circuit architecture. Treat them as project invariants rather than remembered wiki trivia.

## Constant combinators are whole-vector sources

A Factorio 2.x constant combinator is a 1x1 entity that can emit a whole vector of signals: one configured value for every signal lane carried by that vector.

The legacy “20 filters / 20 values per constant combinator” model is not valid for this compiler target. Do not replace it with another remembered fixed limit such as 50 unless that exact bound has been established from the current game/blueprint format or a direct in-game probe.

Consequences:

- Never estimate constant-combinator entity count as `ceil(number_of_signal_lanes / 20)` or `/ 50`.
- Never reject or redesign a vector ROM merely because it contains more than 20 configured signal lanes.
- At the architectural level, treat one constant combinator as one vector-valued source.
- Distinguish vector width from physical entity count. A ROM can grow in supported signal lanes while remaining constant in constant-combinator entities.
- If an exact current upper bound becomes relevant, verify it against the current Factorio 2.x game or blueprint schema and record the verified result here.

This matters especially for item-keyed ROMs and associative lookup designs: signal identity supplies the lane/key space, while one constant combinator can broadcast the whole configured vector.

## Maintenance rule

When a target-game mechanic conflicts with remembered Factorio 1.x behavior, an old wiki entry, or a previous compiler assumption, prefer a current Factorio 2.x in-game probe or current blueprint/schema evidence and update this file.

Record only the strength of the established fact. If the evidence says “whole vector” or “more than 20”, do not manufacture a more specific numeric capacity from memory.
