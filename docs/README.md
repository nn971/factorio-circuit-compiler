# Documentation map

The repository keeps permanent documentation for current contracts and current implementation boundaries. Milestone diaries, branch handoffs, completed plans, and benchmark experiment logs belong in Git/PR history.

## Core source of truth

- `data-contract.md` — logical Level/Event/clock/state semantics.
- `compiler-pipeline.md` — compiler stages and ownership boundaries.
- `factorio-2-circuit-mechanics.md` — verified Factorio 2.x mechanics that constrain architecture.

Read these before changing semantics, lowering, or target-specific architecture.

## Current subsystem references

- `devices.md` — external-device protocols and supported device abstractions.
- `device-anchoring.md` — typed exact-overlap composition for independently generated devices.
- `component-seam-abi.md` — constrained rectangular component boundaries and ordered seam composition.
- `oracles.md` — external/non-deterministic oracle interfaces.
- `state-cell-mapping.md` — physical state-cell realization.
- `technology-mapping.md` — target-aware temporal technology mapping.
- `temporal-alignment.md` — same-token reuse, fresh observation, and exact transport.
- `level-settling-lowering.md` — Level settling/late-placement implementation details.
- `physical-layout.md` — physical-layout strategies, invariants, optimization, and acceptance.
- `safe-crossbar-layout.md` — constructive linear correctness fallback.
- `safe-folded-crossbar-layout.md` — bounded folded constructive fallback.

## Experimental work

- `experimental-temporal-materialization.md` — opt-in phase-free temporal materialization research.
- `experimental-shared-transport.md` — current shared exact-transport/delay-bus contract and validation gate.
- `snake-temporal-optimization.md` — Snake-specific temporal-hypergraph research context.

Experimental documents do not redefine the core data contract. Production modules must not depend on `factorio_circuit.experimental` merely because an experiment is documented here.

## Application notes

- `autonomous-market.md` — autonomous-market controller/application notes.

Benchmark-specific operating instructions and accepted measurements belong with the benchmark itself, especially `benchmarks/snake/README.md` and `benchmarks/snake/baselines.json`.
