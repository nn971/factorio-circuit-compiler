# Selector probes

Keep selector-combinator acceptance artifacts in this benchmark package, not in `src/`. Probes should
exercise one target feature at a time, emit an importable blueprint, print concrete external wiring
requirements, and remain small enough for manual in-game inspection.

Nondeterministic selector modes must not be faked by the deterministic physical simulator. Use
scripted semantic oracle traces for reference behavior and Factorio itself for target acceptance.
