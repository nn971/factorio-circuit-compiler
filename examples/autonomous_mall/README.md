# Autonomous mall prototype

This package is deliberately example-specific. It lives under `examples/` rather than
`src/factorio_circuit/` so mall economics and scheduling policy do not become compiler API.

The branch contains two separate layers:

1. an exact Python reference model for raw-material-efficient quality planning;
2. a grid-snapped transaction prototype for validating the physical worker protocol in Factorio before
   the final circuit-side planner is implemented.

The current slice includes:

- exact-quality commodities `(item, quality)`;
- a runtime-configured raw-material boundary by base item name in the reference planner;
- no-fluid item recipes lowered into separate productivity, quality, and recycler routes;
- global expected material optimization across all requested targets and stocked intermediates;
- physically distinct productivity / quality / recycler worker pools;
- atomic left-to-right input reservations across multiple workers;
- exact expected-value helpers for Factorio's quality roll and 25% recycler return;
- reusable snap-together controller tiles with a stable horizontal electrical ABI;
- complete P/Q/R physical production cells with generated machines, logistics, feeders, and completion
  latches;
- a preassembled `[HEAD][P0][P1][Q0][Q1][R0]` physical row requiring no machine-side manual circuit
  wiring.

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
compiled reservation/worker controller tiles, while `device_tiles.py` composes those controllers with real
Factorio production entities.

## Snap-together physical architecture

The first five-worker monolithic circuit reached physical synthesis but failed wire-color assignment. The
synthesizer correctly found an odd hard-conflict cycle among runtime-open vector nets: that particular
lowered topology could not be represented with Factorio's two circuit-wire colors.

The first workaround split reservation and worker FSMs into separately compiled cells, but hand-wiring all
those pieces was tedious. The current composition boundary is explicit and mechanical:

```text
                 frozen available-material bus
          ------------------------------------------------>

 [ HEAD ][ P0 ][ P1 ][ Q0 ][ Q1 ][ R0 ]
          ------------------------------------------------>
                       control bus
```

Every worker controller contains both:

```text
reservation stage + one-shot worker FSM
```

so `accepted` never crosses a module boundary.

The controller section is 48x48. Matching horizontal docks are 1x1 constant-combinator markers at the exact
same boundary coordinate. Pasting adjacent tiles overlays the marker and adds the new tile's wires while
retaining the existing ones.

Separately compiled modules may choose different internal wire colors and scalar signals. A small ABI
adapter strip hides those choices:

- every public dock is red;
- vector docks use `EACH * 1 -> EACH` isolation;
- scalar device docks rename internal allocated lanes to fixed mall protocol signals.

The complete production-cell generator then extends each worker to 48x72 and consumes the machine-side ABI
locally. Only the horizontal availability/control docks remain a public inter-cell interface.

## Complete production cells

`device_tiles.py` is intentionally example-level blueprint composition rather than compiler-core logic. A
complete P/Q assembler cell contains:

```text
requester chest -> stack-size-1 feeder -> assembling machine 3 -> output inserter -> provider chest
```

P machines request four productivity modules; Q machines request four quality modules. The R cell uses a
recycler with four quality modules.

The generated local device harness provides:

- requester chest `Set requests` from the held one-craft demand;
- green-only assembler Set-recipe input isolated from red machine outputs;
- red machine contents / working / recipe-finished output;
- `positive(requester_demand - machine_contents)` circuit filters for the feeder;
- input enable gated by `not working` so exactly one attempt is fed;
- a durable F/A completion latch so one-tick recipe-finished pulses cannot be missed.

The v1 feeder supports ordinary solid-only recipes with arbitrary ingredient count. Catalyst-like recipes
where one item is simultaneously an ingredient and product are excluded because `Read contents` cannot
distinguish those roles.

Ordinary electric-grid coverage is still external: the generator does not guess where the surrounding
factory's substations or poles belong.

## Generic compiler support

The reusable compiler feature lives in `factorio_circuit.synthesis.interface` and is exported from the
package root:

```python
from factorio_circuit import ModuleInterface, compile_module
```

`ModuleInterface` maps semantic input/output names to exact physical marker coordinates and can attach
Factorio snap-to-grid metadata. The ordinary placer still handles implementation combinators and routing;
only the public boundary geometry is fixed.

This is useful beyond the mall for sensors, displays, train-stop controllers, and other external-device
modules.

## Generate the physical prototype

The convenient complete-cell generator is:

```bash
uv run python -m examples.autonomous_mall.complete_controller \
  > autonomous-mall-complete-blueprint.txt
```

Its blueprint book contains:

```text
0  complete physical row [HEAD][P0][P1][Q0][Q1][R0]
1  complete P productivity worker
2  complete Q quality worker
3  complete R recycler worker
4  controller-only diagnostic row
5  HEAD tile
```

`manual_controller.py` remains available when only the controller layer should be inspected.

See [`manual_in_game.md`](manual_in_game.md) for the complete acceptance procedure.

## Manual batch protocol

HEAD carries fixed virtual control lanes inside one editable vector marker:

```text
signal-D = dispatch
signal-L = launch
```

The first prototype deliberately keeps the settle phase visible:

```text
IDLE    D=0 L=0   snapshot tracks live roboport stock
FREEZE  D=1 L=0   snapshot freezes; reservation row settles
RUN     D=1 L=1   accepted workers execute once
REARM   D=0 L=0   workers re-arm; snapshot tracks live stock again
```

Automating that handshake is a later controller task.

Job configuration is local to each tile. `INPUT job_request` and `INPUT job_recipe` are editable
constant-combinator markers. An empty request disables a worker, so a separate `job_enable` signal is no
longer needed. R0 has no recipe marker because a recycler chooses its process from the inserted item.

Assembler recipes remain latched between transactions. One-shot execution is enforced by starving the
machine once `Read working` rises rather than clearing its recipe, preserving partial productivity-bar
progress across repeated jobs of the same recipe.

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
mask and live inventory, choose the material-efficient next batch, and drive the local job markers
automatically.

The exact LP remains the oracle against which that circuit algorithm should be tested. A local
potential/equilibrium algorithm is acceptable only when its deviations from the LP optimum are understood.

The physical execution boundary is now intentionally boring: once the complete P/Q/R cells pass in-game,
future work can focus on economic job selection and automatic dispatch/launch rather than more hand wiring.

## Validation

Focused tests cover:

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
- named module I/O anchors and grid-snapping metadata;
- independent physical compilation of HEAD / assembler / recycler controllers;
- fixed-red external dock generation and exact shared-dock deduplication;
- generated requester/inserter/machine/provider cells;
- fixed productivity/quality/recycler module roles;
- generated missing-ingredient feeder and durable completion latch;
- physical wire-reference and conservative wire-reach checks for complete cells.
