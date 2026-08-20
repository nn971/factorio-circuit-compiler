# Autonomous mall prototype

This package is deliberately example-specific. It lives under `examples/` rather than
`src/factorio_circuit/` so mall economics and scheduling policy do not become compiler API.

The branch now contains two deliberately separate layers:

1. an exact Python reference model for raw-material-efficient quality planning;
2. a compiled, manually wired transactional controller for testing the physical worker protocol in
   Factorio before the final circuit-side planner is implemented.

The current slice includes:

- exact-quality commodities `(item, quality)`;
- a runtime-configured raw-material boundary by base item name in the reference planner;
- no-fluid item recipes lowered into separate productivity, quality, and recycler routes;
- global expected material optimization across all requested targets and stocked intermediates;
- physically distinct productivity / quality / recycler worker pools;
- atomic input reservations across multiple workers;
- exact expected-value helpers for Factorio's quality roll and 25% recycler return;
- a five-worker compiled transaction controller using roboport stock, requester-chest demands, fixed
  P/Q/R worker roles, one-shot recipe control, and durable external completion latches.

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

The raw boundary is intentionally runtime policy. A future compiled planner can read the mask from a
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
flow. The physical controller executes integer craft/recycle attempts and observes real inventory.
Stochastic work should be issued one attempt at a time, followed by replanning, so expected quality output
never becomes fictitious stock.

The pure-Python `Scheduler` models reservation/campaign behavior. The compiled
`manual_controller.py` tests the corresponding physical transaction discipline with manually supplied
jobs.

## Manual physical controller

Generate the five-worker controller with:

```bash
uv run python -m examples.autonomous_mall.manual_controller \
  > autonomous-mall-manual-blueprint.txt
```

The script prints a concrete signal/wire map on stderr and the importable Factorio blueprint string on
stdout. External machines/chests are intentionally not generated yet.

See [`manual_in_game.md`](manual_in_game.md) for the full wiring and acceptance procedure.

The physical test pool is:

```text
p0, p1  fixed productivity-module assemblers
q0, q1  fixed quality-module assemblers
r0      recycler with quality modules
```

Scheduling uses conservative epochs. A dispatch is accepted only when every worker and completion latch
is idle. Candidate jobs are considered in the order above against one roboport stock snapshot, and each
accepted request is subtracted before the next candidate is considered. The batch then closes until every
accepted transaction finishes. Robot-flight stock changes therefore cannot create a second allocation in
that epoch.

The controller intentionally expects each machine's one-tick recipe-finished signal to be converted to a
persistent external latch. The controller emits a per-worker acknowledgement that clears the latch. This
keeps the first in-game test independent of a final external-device Event protocol.

## Quality mechanics

`quality_mechanics.py` implements the expected-value primitives used by the route builders:

- initial module quality chance `Q`;
- 10% continuation for additional quality tiers after the first successful roll;
- legendary cap;
- recycler expected return of exactly 25% of solid recipe ingredients per recycled product;
- quality upgrading of recycler outputs.

## Remaining controller milestone

The transaction layer is now physically compilable, but job selection is still manual in the in-game
prototype. The missing part is a circuit realization of the economic decision layer: translate actual game
recipe metadata into the route ROM, apply the runtime raw-material mask and live inventory, choose the
material-efficient next batch, and drive the existing transaction ports automatically.

The exact LP remains the oracle against which that circuit algorithm should be tested. A local
potential/equilibrium algorithm is acceptable only when its deviations from the LP optimum are understood.

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
- committing actual stochastic output before reopening a campaign;
- the compiled manual controller's input/state/output contract.
