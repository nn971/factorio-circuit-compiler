# Milestone G differential testing

Milestone G grows the compiler's differential verification surface in small typed layers. Each layer generates deterministic programs inside a supported semantic subset, compiles them through the ordinary production pipeline, and compares semantic execution with physical simulation at the declared output phases.

The random generators are test inputs, not alternative semantics. The semantic simulator remains the reference behavior and the physical simulator remains the independent realization under comparison.

## G1 — scalar combinational programs

**Status: complete.**

G1 generates small acyclic scalar DAGs over a conservative operation vocabulary:

- addition, subtraction, and multiplication;
- bitwise AND, OR, and XOR;
- all six scalar comparisons;
- runtime scalar `select`.

Each fixed program seed is compiled both with optimization disabled and enabled. Input traces are deterministic and deliberately include signed-32-bit boundary values as well as arbitrary `i32` values. Comparison uses the existing tick-aware stream comparator, so physical outputs are observed at their declared phases rather than assuming zero latency.

A failing scalar program is represented independently of the frontend object graph. The greedy reducer can remove extra outputs and bypass expression nodes while repeatedly checking that the mismatch remains, producing a smaller printable witness suitable for a regression test.

G1 landed as commit `2065c8cef281029c6d11d5574d46ec085104504b` through PR #88.

## G2 — vector combinational programs

**Status: current.**

G2 extends the same approach to runtime-open signal vectors. Generated programs combine sparse vector inputs with scalar control inputs and exercise:

- vector addition and subtraction;
- vector/scalar multiplication and negation;
- scalar gating;
- lane-preserving equality/inequality/order filters;
- indexed ascending/descending selector operations.

Input rows contain sparse maps over a small ordinary item-signal catalog. Lane counts and scalar controls use the same boundary-heavy signed-32-bit strategy as G1. Every program is again compiled in optimized and unoptimized modes and compared with the tick-aware semantic/physical stream comparator.

The vector reducer mirrors the G1 structural reducer: it can discard outputs and bypass vector-producing nodes while preserving a failing predicate. Selector latency and other nonzero output phases therefore remain part of the comparison rather than being normalized away by the generator.

## Next layers

After G2 is stable, the generator should add state and clock structure rather than simply making combinational DAGs larger. The intended progression is periodic state first, then Event state and crossings (`sample_on`, gated/derived clocks, event merge), followed by `sum_into` / `hold_into` and output materialization policies. Each extension should retain deterministic seeds and a reducer that understands the added structural dimension.
