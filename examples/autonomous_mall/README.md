# Autonomous mall reference model

This package is deliberately example-specific. It lives under `examples/` rather than
`src/factorio_circuit/` so mall economics and scheduling policy do not become compiler API.

The current slice pins down the controller-independent behavior before building the physical circuit:

- exact-quality commodities `(item, quality)`;
- a runtime-configured raw-material boundary by base item name;
- no-fluid item recipes lowered into separate productivity, quality, and recycler routes;
- global expected material optimization across all requested targets and stocked intermediates;
- physically distinct productivity / quality / recycler worker pools;
- atomic input reservations across multiple workers;
- one outstanding stochastic job per quality campaign, while independent campaigns may run in parallel;
- exact expected-value helpers for Factorio's quality roll and 25% recycler return.

## Economic convention

`MaterialPlanner.plan(targets=..., stock=...)` treats current stock as a free initial endowment and
`targets` as required final balances. If a stocked ingredient is absent from final demand, the planner may
consume it freely. If it is itself demanded, consuming it is allowed only when the plan also replaces it.

The raw boundary is selected by base item name. If `iron-plate` belongs to `raw_items`, every quality tier
of iron plate receives an external-import variable and recipe expansion does not need to continue past
that economic boundary. The objective is

```text
minimize sum(additional imported raw items over every quality)
```

The reference planner solves this as one exact rational material-balance LP. This matters when one route
produces useful coproducts, when several final demands share intermediate stock, or when craft/recycle
loops interact. The optimum is global within the supplied expected-route model rather than greedy per
requested item.

The raw boundary is intentionally runtime policy. A future compiled controller can read the mask from a
constant combinator; the reference planner simply accepts it as a Python set.

## Routes and worker roles

`ProductionRoute` is an arbitrary expected vector transformation:

```text
quality-qualified input vector -> quality-qualified output vector
```

Fractions are deliberate because quality/recycling policies are stochastic. `routes.py` supplies the
first concrete no-fluid recipe lowering:

- `productivity_route(...)` preserves exact ingredient quality and applies a supplied productivity bonus;
- `quality_route(...)` exposes every possible quality outcome in one expected output vector;
- `recycler_route(...)` reverses one product into all expected quality-qualified solid ingredients.

`WorkerKind` keeps the three physical pools separate:

```text
PRODUCTIVITY  fixed productivity modules
QUALITY       fixed quality modules
RECYCLER      recycler with quality modules
```

No worker changes module role dynamically.

## Expected planning versus physical jobs

The LP is the economic reference model. Its route usage can be fractional and describes expected material
flow. The physical controller must execute integer craft/recycle attempts and observe actual outcomes.
For stochastic quality work, the intended rule is one atomic attempt, observe the real quality-qualified
output, update stock, then replan. This prevents predicted quality outcomes from becoming fictitious
inventory.

The scheduler model therefore uses `Job` for one atomic physical attempt rather than directly executing a
fractional LP route count.

## Oscillation policy

Two mechanisms are explicit in the reference scheduler:

1. **Reservation:** once a job is assigned, its whole ingredient vector is unavailable to every other
   worker immediately.
2. **Campaign lock:** only one stochastic job for a given quality campaign may be outstanding. A second
   quality assembler can still run an unrelated campaign.

The eventual in-game worker protocol should retain a reservation until the worker transaction has
reported its actual outputs. Roboport stock changes during robot flight therefore do not create duplicate
jobs.

## Quality mechanics

`quality_mechanics.py` implements the expected-value primitives used by the route builders:

- initial module quality chance `Q`;
- 10% continuation for additional quality tiers after the first successful roll;
- legendary cap;
- recycler expected return of exactly 25% of solid recipe ingredients per recycled product;
- quality upgrading of recycler outputs.

## What remains for the physical mall

This branch intentionally stops before compiling the whole controller. The next layer should translate
actual game recipe metadata into `ItemRecipe` records, enumerate the available route set for the installed
module configuration, and implement the transactional requester-chest worker protocol with circuit Events.
The economic and scheduling behavior in this package is the reference contract for that circuit.

## Validation

Focused tests under `tests/examples/autonomous_mall/` cover:

- raw-boundary changes without changing the recipe book;
- protected final stock and zero-cost surplus stock;
- exact rational LP behavior, including negative stock balances and infeasibility;
- a joint-demand example where global optimization beats greedy per-target planning;
- explicit productivity/quality route separation;
- quality probability and recycler expectation math;
- true multi-output recycler routes;
- reservation conflicts across multiple workers;
- worker-pool separation;
- stochastic campaign locking and independent parallel quality campaigns;
- committing actual stochastic output before reopening a campaign.
