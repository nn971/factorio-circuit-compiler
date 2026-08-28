# Milestone D2: transactional rigid-component translation

Milestone D1 makes component geometry authoritative while preserving the current component pose. D2
adds bounded physical motion without weakening that contract.

## Supported motion

D2 supports **translation only**. A proposal changes one component origin while preserving its
`quarter_turns` value and every member's local offset. The translation delta must be an integral
number of tiles on both axes, which preserves the physical coordinate phase of every already-valid
member.

Quarter-turn changes are intentionally deferred. The current `Layout` representation records entity
centres but does not yet carry a general physical orientation contract for arbitrary 2x1 combinators.
Rotating their coordinates without rotating their entity orientation would create a false notion of
rigid-body validity.

## Transaction contract

`translate_rigid_component_transactionally(...)` operates on an exact-valid
`ComponentLayoutOptimizationProblem`:

1. Resolve the target component and verify the requested origin.
2. Move every member to the coordinate implied by the same rigid translation.
3. Recompute all component footprints, keepouts, adapter reservations, and fixed member positions.
4. Validate implementation-only geometry before routing. Stale relays are deliberately absent from
   this check.
5. Rebuild the relay workspace from the candidate component-aware legal lattice.
6. Discard the complete old relay scaffold and route the logical physical networks from scratch.
7. Simplify the fresh relay topology to a fixed point.
8. Materialize the candidate `Layout` and run the full D1 + exact physical validators.

Any failure returns the original exact-valid component problem unchanged. No partially moved component
or partially rebuilt relay topology can escape the transaction.

## Bounded automatic optimization

`optimize_rigid_component_translations(...)` performs deterministic coordinate descent over components
with finite `allowed_origins` lists.

For each pass and component it evaluates at most `max_candidates_per_component` alternatives. A
candidate is accepted only if its exact public physical objective

```text
(relay_count, occupied_area, wire_length)
```

strictly improves. Strict improvement prevents cycles, while `max_passes` supplies an independent
hard work bound. Components whose `allowed_origins` is `None` remain manually translatable through the
transaction API but are skipped by automatic search because their domain is not finite.

## Relationship to later Milestone D work

D2 solves rigid placement, not interface realization. D3 still needs anchored external interface
routing with guaranteed adapter workspace. D4 can then integrate real devices such as assemblers using
these generic geometry and motion primitives.
