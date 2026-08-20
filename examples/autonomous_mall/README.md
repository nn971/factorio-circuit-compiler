# Autonomous mall prototype

This package is deliberately example-specific. It lives under `examples/` rather than
`src/factorio_circuit/` so mall economics and scheduling policy do not become compiler API.

The branch contains two separate layers:

1. an exact Python reference model for raw-material-efficient quality planning;
2. a manually wired set of small compiled transaction cells for validating the physical worker protocol
   in Factorio before the final circuit-side planner is implemented.

The current slice includes:

- exact-quality commodities `(item, quality)`;
- a runtime-configured raw-material boundary by base item name in the reference planner;
- no-fluid item recipes lowered into separate productivity, quality, and recycler routes;
- global expected material optimization across all requested targets and stocked intermediates;
- physically distinct productivity / quality / recycler worker pools;
- atomic chained input reservations across multiple workers;
- exact expected-value helpers for Factorio's quality roll and 25% recycler return;
- modular stock-snapshot, reservation, assembler-worker, and recycler-worker circuits.

## Economic convention

`MaterialPlanner.plan(targets=..., stock=...)` treats current stock as a free initial endowment and
`targets` as required final balances. If a stocked ingredient is absent from final demand, the planner may
consume it freely. If it is itself demanded, consuming it is allowed only when the plan also replaces it.

The raw boundary is selected by base item name. If `iron-plate` belongs to `raw_items`, every quality tier
of iron plate receives an external-import variable. The objective is

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
flow. Physical workers execute integer craft/recycle attempts and observe real inventory. Stochastic work
should be issued one attempt at a time, followed by replanning, so expected quality output never becomes
fictitious stock.

The pure-Python `Scheduler` models reservation/campaign behavior. `manual_controller.py` contains the
physical templates used to validate the same discipline in game.

## Why the physical prototype is modular

The first five-worker monolithic circuit reached physical synthesis but failed wire-color assignment. The
synthesizer treats unrelated runtime-open vector nets sharing one physical connector as hard conflicts.
Factorio supplies only red and green wire, so an odd conflict cycle is not physically realizable without
additional isolation hardware.

Rather than weakening that safety rule, the prototype now uses explicit physical composition:

```text
roboport
   |
stock snapshot
   |
reservation p0 -> p1 -> q0 -> q1 -> r0
       |          |     |     |     |
      p0         p1    q0    q1    r0
    worker     worker worker worker worker
```

One reservation template is pasted five times. Its `remaining` vector is manually wired into the next
cell's `available` input. This makes the reservation boundary physically explicit and prevents the compiler
from having to synthesize an impossible multi-open-vector connector.

## Manual physical prototype

Generate the template blueprint book with:

```bash
uv run python -m examples.autonomous_mall.manual_controller \
  > autonomous-mall-manual-blueprint.txt
```

The book contains:

```text
1 x stock snapshot
5 x reservation cell (paste the one template five times)
4 x assembler worker (paste the one template four times)
1 x recycler worker
```

The script prints each template's concrete signal/wire map on stderr. External machines/chests and the
inter-template wiring are intentionally manual for this milestone.

See [`manual_in_game.md`](manual_in_game.md) for the complete wiring and acceptance procedure.

The batch protocol uses two manual persistent controls:

```text
IDLE    dispatch=0 launch=0   snapshot tracks live roboport stock
FREEZE  dispatch=1 launch=0   snapshot freezes; reservation chain settles
RUN     dispatch=1 launch=1   accepted workers execute once
REARM   dispatch=0 launch=0   workers re-arm; snapshot tracks live stock again
```

This explicit settle phase avoids making clock-phase assumptions between independently compiled cells.
Automating that handshake is a later device-protocol task.

Assembler recipes remain latched between transactions. One-shot execution is instead enforced by an
external stack-size-1 ingredient feeder gated by the worker's `input_enable` and the machine's local
Read-working signal. This preserves partial productivity-bar progress across repeated jobs of the same
recipe.

Each machine's one-tick recipe-finished signal is converted to a persistent external latch. The worker emits
an acknowledgement that clears the latch, so zero-output recycler attempts still complete reliably.

## Quality mechanics

`quality_mechanics.py` implements the expected-value primitives used by the route builders:

- initial module quality chance `Q`;
- 10% continuation for additional quality tiers after the first successful roll;
- legendary cap;
- recycler expected return of exactly 25% of solid recipe ingredients per recycled product;
- quality upgrading of recycler outputs.

## Remaining controller milestone

Job selection is still manual in the in-game prototype. The missing part is a circuit realization of the
economic decision layer: translate actual game recipe metadata into a route ROM, apply the runtime raw
mask and live inventory, choose the material-efficient next batch, and drive the reservation/worker cells
automatically.

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
- independent physical compilation of the four manually composed template circuits.
