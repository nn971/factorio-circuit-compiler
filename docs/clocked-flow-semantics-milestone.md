# Clocked Flow Semantics Milestone

## Status

This document records the agreed direction for the next semantic milestone of the Factorio circuit compiler.

The milestone is motivated by the autonomous-market prototype, which exposed an awkward property of the current timing model: short-lived external events can disappear when a logical state domain has an inferred physical period greater than one game tick. The goal is to replace this special-case problem with a more general semantic model that treats time, events, clock domains, feedback, and future external-device protocols coherently.

The current frontend uses `.sample()` for observations. `.value` remains a deprecated compatibility
alias for registers and must not be removed by this milestone; new code should use `.sample()`. The
existing sampled IR nodes and their downstream consumers remain compatibility representations while
the broader clocked-flow model is deferred.

---

## 1. Core semantic split

External sources have two independent properties:

```text
payload shape
    SCALAR
    VECTOR

temporal modality
    LEVEL
    EVENT
```

Thus the source classification is conceptually

\[
\text{Source} = \text{PayloadShape} \times \text{TemporalModality}.
\]

### Level

A `Level[T]` is persistent physical-time information. A value exists at every game tick.

Typical examples:

- chest contents;
- assembler ingredients;
- assembler working state;
- temperature;
- train contents;
- accumulator charge.

The natural cross-clock interpretation of a level is sampling.

### Event

An `Event[T]` represents discrete occurrences carrying an optional payload.

Typical examples:

- craft-finished pulses;
- inserter pulse mode;
- belt item-passed pulses;
- request arrivals;
- completion notifications.

The natural cross-clock interpretation of an event is preservation or aggregation over an interval.

### Why clocks do not replace Level/Event

A level and an event may both physically change every game tick and therefore share the same underlying tick clock, while requiring different semantics when viewed on a slower clock.

For a level:

\[
\operatorname{sample}_C(x)
\]

means the value present at the target activation.

For an additive event stream:

\[
\operatorname{sum}_C(E)
\]

means the sum of all event payloads since the previous target activation.

Therefore:

> A clock describes **when values are present**.
> Modality describes **how values behave under re-clocking**.

The Level/Event distinction should survive through semantic clock analysis, but can largely disappear after every cross-clock operation has been made explicit.

---

## 2. Clocked flows

Ordinary logical computation operates on clocked flows rather than directly on raw Level/Event sources.

Conceptually:

\[
\mathrm{Flow}[T,C]
\]

is a logical sequence

\[
x_C[0],x_C[1],x_C[2],\ldots
\]

indexed by occurrences of clock \(C\).

Let the physical activation times of \(C\) be

\[
\tau_C(0),\tau_C(1),\tau_C(2),\ldots
\]

The logical index \(k\) counts clock occurrences rather than Factorio game ticks.

A useful conceptual flow record is:

```text
Flow
    payload_shape
    modality
    base_clock
    logical_offset
```

The exact Python representation can remain simpler than this conceptual model.

---

## 3. Clocks

A clock is essentially an activation-event stream:

\[
\mathrm{Clock} \sim \mathrm{Event[Unit]}.
\]

A clock states that a logical reaction occurs at particular physical instants.

Examples:

```text
periodic clock:
*---*---*---*---*

external event clock:
-*------*-*-------*
```

Periodic and event-driven state should use the same logical transition model.

The difference appears during physical timing:

- an inferred internal clock can often be slowed by the compiler;
- an external event clock is constrained by the environment.

### Initial clock kinds

The milestone should support at least the conceptual distinction:

```text
InferredClock
FixedPeriodicClock
ExternalEventClock
DerivedClock
```

These need not necessarily be separate user-visible classes.

---

## 4. Clock timing contracts

Each clock should carry a guarantee such as:

```text
guaranteed_min_separation
```

with semantics

\[
\tau_C(k+1)-\tau_C(k)\ge m_C.
\]

A recurrence or stateful computation independently derives a requirement:

```text
required_min_separation
```

and direct realization is legal when

\[
m_C \ge r.
\]

For an inferred compiler-controlled clock, the compiler may enlarge its period/minimum separation until the timing requirement is met.

### Derived clocks

Clock operations propagate the guarantee conservatively.

#### Logical shift

A logical reindexing does not change the clock spacing.

#### Gating/subclock

Removing activations cannot reduce the true minimum spacing, so retaining the parent's known lower bound is safe.

#### Intersection

Likewise, a derived intersection can safely inherit the known lower bound unless stronger information is available.

#### Merge/union

A union can place activations closer together.

For example, two individually 10-tick periodic clocks may interleave to produce 5-tick spacing or even 1-tick spacing.

Therefore:

\[
m(C_1\cup C_2)
\]

cannot generally be inferred as merely \(\min(m_1,m_2)\).

When phase/subclock relations are known, compute the stronger result. For unrelated external clocks in Factorio's discrete-time model, a conservative merged guarantee is typically one game tick, with simultaneous activations coalesced according to the merge semantics.

### Possible later extension

`min_separation` captures direct recurrence feasibility but does not describe bursts fully. A future richer contract may express constraints such as:

```text
at most N activations in every W ticks
```

This milestone should deliberately begin with minimum separation only.

---

## 5. Logical steps become flow-local reindexing

The global mutable logical cursor should disappear from the semantic core.

Instead of:

```python
circuit.step()
```

the model should support:

```python
x.step()
x.step(n)
```

with meaning

\[
x_C[k].\operatorname{step}(n)=x_C[k+n].
\]

This is a **pure logical reindexing** operation.

Internally, it is preferable to preserve the same base clock and adjust an occurrence offset rather than manufacture an unrelated clock.

For example:

```text
Flow(base=x, clock=C, offset=0)
    .step(2)

→ Flow(base=x, clock=C, offset=2)
```

### `.step()` is not a delay

The distinction must remain strict:

```python
x.step()
```

refers to a different logical occurrence.

A stateful delay means something like

\[
y[k+1]=x[k]
\]

and therefore introduces memory.

The compiler should never silently turn `.step()` into a physical delay/register.

This preserves the distinction between logical indexing and physical game-tick timing.

---

## 6. Atomic logical reactions

Each activation \(C[k]\) represents one atomic logical reaction:

```text
1. observe inputs associated with C[k]
2. observe state S[k]
3. evaluate ordinary logical expressions
4. determine state updates
5. logically produce S[k+1]
```

Physically, the realization may require several Factorio ticks.

Semantically:

\[
S[k+1]=F(S[k],x[k]).
\]

This rule is identical for periodic and event-driven clocks.

---

## 7. Logical causality versus physical execution time

Every dependency should carry two independent quantities:

```text
logical displacement d
physical latency L
```

Ordinary combinational operations preserve logical index:

\[
d=0.
\]

They may still incur positive physical latency.

A state transition advances logical time:

\[
S[k] \rightarrow S[k+1],
\]

so it contains positive logical displacement.

### Fundamental causality rule

> Every directed feedback cycle must contain strict logical advance.

Equivalently, a feedback cycle consisting entirely of zero-logical-displacement edges is noncausal.

Thus:

\[
x[k]\rightarrow \cdots \rightarrow x[k]
\]

through positive-latency combinational logic is rejected.

But:

\[
S[k]\rightarrow \cdots \rightarrow S[k+1]
\]

is logically causal.

This should become an explicit causality-analysis phase distinct from physical timing.

---

## 8. Physical timing of a logical dependency

For a dependency spanning \(d\) occurrences of clock \(C\) and requiring physical latency \(L\), the generalized timing condition is:

\[
\tau_C(k+d)-\tau_C(k)\ge L
\]

with the exact constant adjusted for Factorio write-stage conventions.

For a periodic clock:

\[
\tau_C(k)=\phi+kP,
\]

so this reduces to a constraint of the form

\[
dP\ge L.
\]

This generalizes the existing timing solver, which already reasons using logical displacement and physical latency.

A useful milestone goal is therefore to preserve the current difference-constraint machinery while replacing the assumption that every domain is a rigid periodic sequence.

---

## 9. Event-driven feedback and throughput

An event-driven recurrence can be logically causal yet physically unrealizable at the requested arrival rate.

Example:

```text
external event clock:
    guaranteed_min_separation = 1

feedback recurrence:
    required_min_separation = 4
```

The recurrence is semantically meaningful, but direct realization cannot accept every event.

This is a throughput violation rather than a causality error.

The compiler may resolve the situation only by one of the following:

1. prove a stronger arrival-spacing contract;
2. use an explicit stateful bridge such as an accumulator or queue to move into another execution clock;
3. report an unsatisfied throughput constraint.

The compiler must preserve declared event semantics and must not silently drop activations.

---

## 10. Arrival clock versus execution clock

A useful future pattern is:

```text
external event clock
        ↓
clock bridge / buffer
        ↓
internal schedulable clock
        ↓
slow feedback controller
```

For example, an autonomous-market worker may emit completion events at an external clock while the controller runs on a slower compiler-controlled clock.

This is precisely where operations such as `sum_into()` become useful.

---

## 11. Clock normalization

Ordinary operators should eventually operate entirely within one compatible clock.

For an expression such as:

```python
state.set(a + b * c)
```

the surrounding state transition supplies the natural target/evaluation clock.

Clock inference should therefore proceed contextually:

```text
expected clock = state.clock

a ─ normalize to C ─┐
b ─ normalize to C ─┼── ordinary expression on C
c ─ normalize to C ─┘
```

This is preferable to pairwise clock alignment such as arbitrarily converting `a` to `b` or `b` to `a`.

After clock normalization:

> Every ordinary logical expression region is single-clock, and every cross-clock dependency is represented by an explicit bridge operation.

This prevents combinatorial clock-bridge explosion.

---

## 12. Stateless versus stateful clock conversions

Different clock mismatches should be classified according to whether history is required.

### Common stateless conversions

Examples include:

- sampling a Level on a subclock;
- using an Event payload on its own occurrence clock;
- certain additive event merges using zero as the absent identity;
- known restrictions from a parent clock to a gated/subclock.

These should normally be silent.

### Stateful conversions

A crossing that must preserve information across multiple source activations requires memory.

The central example is:

\[
\operatorname{sum}_C(E)[k]
=
\sum_{\tau_C(k-1)<t\le\tau_C(k)}E(t).
\]

This is the natural event-to-slower-clock bridge.

A public API might eventually use a name such as:

```python
event.sum_into(clock)
```

rather than a parameterless `.sum()`, because re-clocking is directional and the destination clock is essential.

`count_into(clock)` is derivable by summing unit payloads.

### Diagnostics policy

The initial milestone should be conservative:

- same-clock and canonical stateless conversions: silent;
- explicitly requested stateful conversion: silent;
- implicit stateful conversion, if later supported: one diagnostic per shared bridge;
- ambiguous conversion: require the user/compiler context to specify the target clock or policy.

A bridge-level diagnostic should not be repeated at every use site.

---

## 13. Bridge sharing and optimization

Clock bridges must be semantic IR objects rather than immediately emitted physical circuits.

Equivalent bridges should be interned/shared.

Conceptually, a bridge identity may depend on:

```text
source flow
source clock
target clock
conversion policy
```

All users of the same conversion should reference one bridge.

Important optimizations include:

### Keep vectors packed

An `Event[Vector]` should cross a clock through one vector accumulator rather than one scalar accumulator per signal lane.

### Merge additive events before accumulation

When legal:

\[
\operatorname{sum}_C(E_1+E_2)
=
\operatorname{sum}_C(E_1)+\operatorname{sum}_C(E_2).
\]

Prefer:

```text
E1 ─┐
    ├─ merge ─ one accumulator ─► C
E2 ─┘
```

over independent accumulators.

### Fuse bridges with state

A state transition such as a lifetime accumulator should avoid an unnecessary intermediate event accumulator when the destination register can directly absorb the event contribution.

Bridge insertion must therefore happen early enough for state realization/optimization to fuse structures.

---

## 14. Clock relations and small clock algebra

Clocks should expose structural relationships rather than behaving as opaque IDs.

Useful concepts include:

\[
C_1\preceq C_2
\]

for "C1 is a subclock of C2", together with operations such as:

```text
gate(clock, predicate)
merge(clock_a, clock_b)
```

A future clock algebra may include unions and intersections more formally.

Operator semantics can also influence clock inference.

For example, additive event streams naturally admit absence-as-zero and union clocks. Ordinary comparisons usually require co-presence or an explicit re-clocking rule.

The first milestone should avoid building a full abstract algebra/typeclass system, while leaving the IR capable of representing these distinctions.

---

## 15. Event clocks and state updates

Event-triggered state updates should be first-class rather than forced through periodic sampling.

A state transition on an event clock is simply:

\[
S_C[k+1]=F(S_C[k],x_C[k]).
\]

For example, a completion counter can update once per craft-finished occurrence.

Levels can be sampled on that event clock:

```text
contents : Level[Vector]
finished : Event[Unit]

sample contents on finished.clock
        ↓
Flow[Vector, FinishedClock]
```

This naturally represents:

> Whenever crafting finishes, observe the current contents and update state.

Conversely, when an event source must feed a different clock, an explicit bridge such as `sum_into()` or `hold_into()` defines the cross-clock semantics.

---

## 16. Sampling and current `.sample()` semantics

The current frontend uses `.sample()` as the correct observation API.

During this milestone, `.sample()` should be treated as a compatibility/front-end construct rather than the final internal semantic primitive.

The eventual model is:

```text
Level source
    + target clock
    ↓
clocked flow
```

and

```text
Event source
    ↓
flow on its occurrence clock
```

This should replace the current special IR families such as scalar/vector source plus sampled scalar/vector source wherever possible.

---

## 17. Feed-forward pipelines and snapshotting

A triggered logical reaction does not automatically require a sample-and-hold register.

A physical combinator pipeline can naturally carry the value observed at the activation tick through later stages while the activation/valid stream is delayed to match.

Conceptually:

```text
source payload ─ stage1 ─ stage2 ─ stage3 ─► result
activation      ─ delay  ─ delay  ─ delay  ─► valid
```

A register is needed when persistence, buffering, re-use, or a true temporal bridge requires memory, rather than merely because feed-forward computation takes multiple physical ticks.

This distinction can significantly reduce unnecessary combinators.

---

## 18. Output materialization / padding policy

Internally, a flow is sparse: semantic values exist at its clock activations.

A Factorio circuit wire carries a dense signal vector every game tick.

Therefore every exported flow requires a materialization policy.

### HOLD

Retain the most recent activation value between activations.

```text
activation:  *       *       *
value:       3       8       2

wire:        3 3 3 3 8 8 8 8 2 ...
```

Natural default for Level-like output.

### ZERO

Emit zero between activations.

```text
activation:  *       *       *
value:       3       8       2

wire:        3 0 0 0 8 0 0 0 2 ...
```

Natural for additive event streams and pulse-style interfaces.

### VALID

Expose a payload together with an explicit presence/valid signal.

```text
payload:  3  ?  ?  ?  0  ? ...
valid:    1  0  0  0  1  0 ...
```

Useful when zero is a meaningful payload or when downstream consumers must distinguish absence from a zero-valued event.

### Initial defaults

Conceptually:

```text
LEVEL          → HOLD
additive EVENT → ZERO
general EVENT  → VALID
```

The selected policy should become explicit at circuit/device boundaries rather than changing internal Flow semantics.

---

## 19. Proposed Level/physical semantic/compiler pipeline

The following describes the Level/physical compilation pipeline, not the semantic/reference Event
lane. Event-bearing circuits still undergo frontend elaboration and semantic `CircuitModule`
construction, then leave this route at the explicit Event boundary.

The milestone should move toward:

```text
ordinary Python elaboration
        ↓
source / flow construction
  - payload shape
  - modality
  - clock provenance
  - logical offsets
        ↓
clock inference and normalization
  - infer evaluation clocks
  - derive clock relations
  - insert explicit clock bridges
        ↓
clocked logical IR
  - Flow[T,C]
  - State[T,C]
  - explicit clock crossing nodes
        ↓
causality analysis
  - dependency logical displacement
  - reject zero-advance cycles
        ↓
physical timing analysis
  - combinator latency
  - feedback required separation
  - clock timing guarantees
  - physical phases
        ↓
bridge and state realization
        ↓
logical/target optimization
        ↓
Abstract Physical IR
        ↓
physical synthesis
        ↓
Layout
        ↓
blueprint serialization
```

The exact ordering of bridge/state optimization can evolve, but clock normalization and causality must happen before target combinator realization.

---

# Implementation plan

## Stage 1 — Introduce the semantic clock vocabulary

Add semantic representations for:

```text
PayloadShape
TemporalModality
Clock
clock provenance / relations
Flow
logical occurrence offset
```

Adapt existing inputs through compatibility wrappers.

Current scalar/vector inputs should initially behave as Level sources.

No major target/backend behavior change is required yet.

### Tests

- scalar/vector Level source construction;
- Event source construction;
- clock identity/provenance;
- shape/modality preservation;
- basic clock guarantees.

---

## Stage 2 — Prepare flow-local indexing internally

This delivery preserves the existing mutable ``Circuit.step()`` compatibility behavior.  It does
**not** expose ``flow.step()`` or any other public flow-local reindexing API.  Public Flow
reindexing is deferred until flows are end-to-end composable, simulatable, and lowerable.

Implement the internal semantic groundwork needed for a later flow-local API:

```python
logical displacement
```

as metadata extracted from existing state requirements, without changing frontend indexing or
introducing physical delay.

Keep existing register-read logical offsets and `.sample()` lowering in their legacy representation
for this delivery.  Generalized flow-local indexing and migration to Flow references are deferred
alongside public Flow reindexing until flows are end-to-end composable, simulatable, and lowerable.

Temporarily retain compatibility frontend behavior where useful.

### Tests

- logical displacement on state dependencies;
- no public ``flow.step()`` or frontend flow-local reindexing;
- existing ``Circuit.step()`` compatibility behavior;
- non-positive state-recurrence cycle detected by causality analysis; startup/warm-up diagnostics
  remain a separate compatibility concern.

---

## Stage 3 — Separate causality analysis from physical timing

Extract logical dependency analysis from the current timing solver while retaining the existing
frontend and periodic timing interfaces.  ``Circuit.step()`` remains the compatibility cursor, and
public Flow reindexing remains deferred until flows are end-to-end composable, simulatable, and
lowerable.

For this delivery, the extracted graph contains only ordinary state-register dependencies.  Clock
relations, Event behavior, and cross-clock semantics remain future work.

Every dependency should expose:

```text
state-register relation
logical displacement
```

Causality analysis rejects directed recurrence cycles with non-positive total logical displacement,
independently of physical latency.

Then adapt physical timing to consume the generalized dependency graph.

Preserve the current difference-constraint machinery where possible.

### Tests

- legal one-step recurrence;
- illegal same-occurrence combinational feedback;
- multiple state variables in one feedback SCC;
- feed-forward long pipeline accepted;
- logical legality independent from eventual throughput legality.

---

## Phase 3 delivery boundary — semantic/reference-only Events

Phase 3 provides declared scalar/vector Event sources, deterministic schedules, and a separate
semantic `simulate_events(...)` reference path. One `FreezeReg.capture_on(...)` operation can capture
zero-offset Level/state values or a vector Event payload with atomic same-timestamp reactions and a
declared-throughput check.

This is not physical Event support. Event-bearing circuits undergo frontend elaboration and semantic
`CircuitModule` construction. Level/physical routes then raise `EventCompilationError` before
`StateTimingPlan` or semantic-to-physical lowering; use `simulate_events(...)` for the tested
reference behavior. Phase 4 now provides reference-only SampleOn observations and Event/SampleOn
materialization, but physical pulse capture, buffering, handshake/ready semantics, Event/periodic
mixing, bridges, physical output policies, valid wiring, and autonomous-market migration remain
unresolved.

---

## Stage 4 — Clock contracts and event-driven state (deferred)

The semantic Event source/schedule/reference subset is complete, but this stage's generalized Event
clock taxonomy is not. Event-bearing circuits are elaborated into semantic `CircuitModule` values;
Level/physical routes then reject them before `StateTimingPlan` or semantic-to-physical lowering.

Introduce clock guarantees:

```text
guaranteed_min_separation
```

and derive feedback requirements:

```text
required_min_separation
```

Support at least:

```text
InferredClock
FixedPeriodicClock
ExternalEventClock
DerivedClock
```

Event clocks should be able to drive ordinary state transitions directly.

### Tests

For a recurrence requiring separation `R`:

```text
min_separation = R     → accepted
min_separation = R + 1 → accepted
min_separation = R - 1 → throughput error
```

For inferred clocks, verify that the compiler enlarges the period appropriately.

---

## Stage 5 — Clock normalization and explicit bridges (partial/deferred)

Semantic `SampleOn` is complete only as a non-expression, reference-only raw Level observation on a
declared Event clock, with source-derived payload shape. `EventMerge`, `SumInto`, `HoldInto`, and
general Event/periodic mixing remain deferred. Physical bridges, bridge CSE/packing, and implicit or
stateful crossing realization are not implemented.

The eventual crossing vocabulary is expected to include:

```text
SampleOn
EventMerge
SumInto
HoldInto
GateClock
```

Normalize entire expression regions according to consumer-selected expected clocks.

Do not immediately infer arbitrary stateful conversions. Keep history-creating crossings explicit initially.

Bridge interning/CSE and packed bridge realization remain future work.

### Optimizer requirements

- one shared bridge for repeated use;
- keep vector bridges packed;
- merge additive events before `SumInto`;
- fuse bridges with compatible state where possible.

---

## Stage 6 — Output materialization (partial: reference-only)

Reference-only Event and SampleOn materialization is complete through `materialize_event_trace(...)`
with HOLD/ZERO/VALID policies over a timestamp domain. This does not implement physical output
policies, dense Event outputs, valid wiring, storage, or Factorio hardware behavior.

Future physical/output work may add explicit output policies:

```text
HOLD
ZERO
VALID
```

Eventually make every exported physical clocked flow choose or infer a materialization policy; the
current implementation only materializes Event/SampleOn reference results.

This should also become the basis for future external-device protocol output/input requirements.

### Tests

- sparse Level flow → HOLD;
- additive Event flow → ZERO;
- general Event flow → VALID;
- payload/valid phase alignment;
- irregular clocks.

---

## Stage 7 — Documentation cleanup and compatibility audit

Update:

- semantic model documentation;
- architecture documentation;
- state/timing documentation;
- autonomous-market notes.

Retain and clarify the timing-open-problems material for the deferred physical and clock-contract work.
This documentation delivery removes no compatibility surface.

Do not remove compatibility IR/API pieces: `.value`, rejected tick shims, `compile_abstract_circuit`,
the legacy compiler, sampled IR nodes, Event fields, and SampleOn are retained public/tested behavior.

---

# Stress-test examples

The new semantic layer needs scalable structured benchmarks analogous to sorting and WHT for physical synthesis.

## A. Multi-rate event ledger

This should be the flagship benchmark.

Use \(N\) independent `Event[Vector]` producer streams:

\[
E_0,\ldots,E_{N-1}.
\]

Merge them:

\[
E=E_0+\cdots+E_{N-1}.
\]

Feed the merged stream to several reporting clocks:

\[
A_j=\operatorname{sum}_{C_j}(E).
\]

Also maintain a lifetime total directly from the event stream.

Conceptually:

```text
worker 0 ─┐
worker 1 ─┤
worker 2 ─┼─ merge ─┬─ sum_into(fast_report)
...       │         ├─ sum_into(slow_report)
worker N ─┘         └─ sum_into(audit_report)
```

### Features stressed

- many independent event clocks;
- clock merging;
- conservative merged `min_separation`;
- vector event payloads;
- stateful cross-clock accumulation;
- bridge sharing;
- event merge before bridge;
- feedback;
- output padding/materialization.

### Important scaling invariant

Circuit growth should depend mainly on actual clock-domain crossings, not on the number of downstream expression uses.

---

## B. CIC-\(N,R\) decimator

Use a cascaded integrator-comb filter.

A fast clock drives an \(N\)-stage integrator chain with feedback.

A slow subclock occurs every \(R\)-th fast activation.

The slow side runs the comb stages.

Conceptually:

```text
fast clock:
x → integrator → integrator → ... → integrator
                                      │
                                decimate by R
                                      │
slow clock:
         comb ← comb ← ... ← comb
```

Integrator recurrence:

\[
I_j[k+1]=I_j[k]+I_{j-1}[k].
\]

Comb recurrence:

\[
D_j[k]=D_{j-1}[k]-D_{j-1}[k-M].
\]

### Parameters

```text
N = stage count
R = decimation ratio
M = differential delay
```

### Features stressed

- `.step()` logical offsets;
- legal feedback SCCs;
- physical recurrence timing;
- known subclock relationships;
- multirate re-clocking;
- large structured state graphs;
- output HOLD semantics.

---

## C. Event-clocked linear recurrence

Define a large state vector driven directly by an external event clock:

\[
x[k+1]=Ax[k]+Bu[k].
\]

A simple scalable structured choice is:

\[
x_i[k+1]
=
x_i[k]+x_{i-1}[k]+x_{i+1}[k].
\]

The compiler derives a minimum recurrence separation \(R\).

Then test external clock contracts around the boundary.

### Features stressed

- first-class event-driven state;
- feedback timing;
- `guaranteed_min_separation`;
- `required_min_separation`;
- throughput failure diagnostics;
- transition into a slower schedulable clock through `SumInto`.

This is the primary benchmark for checking that causality and throughput errors remain distinct.

---

## D. Token-bucket / credit arbiter

Use \(N\) asynchronous request-event sources and a periodic refill source.

Maintain credit state:

\[
credit[k+1]
=
credit[k]+refill-consumed.
\]

Requests are merged/arbitrated and become accepted/rejected event streams.

Conceptually:

```text
req0 ─┐
req1 ─┤
req2 ─┼─ merge/arbitrate ─ feedback credit state
...   │
reqN ─┘
```

### Features stressed

- clock merge;
- gating/subclocks;
- simultaneous-event semantics;
- event filtering;
- level sampling on event clocks;
- feedback;
- event outputs;
- ZERO/VALID materialization.

### Useful invariant

\[
initial\_credit + total\_refill
=
remaining\_credit + total\_accepted.
\]

---

## E. Clock-bridge fanout adversarial benchmark

Construct one cross-clock event flow:

```python
slow = events.sum_into(slow_clock)
```

and reuse it throughout a large expression DAG.

The compiler should synthesize exactly one semantic/physical bridge.

A second version should use many event producers:

\[
E_1+\cdots+E_N
\]

and verify that the optimizer merges them before crossing when legal.

### Regression goals

Prevent:

- one accumulator per use site;
- one accumulator per scalar signal lane;
- redundant equivalent clock bridges;
- failure to factor additive event crossings.

---

# Milestone completion criteria

The milestone is complete when the compiler can correctly represent, simulate, analyze, and lower a system containing:

```text
external Level and Event sources
        ↓
multiple clock domains
        ↓
event-clocked state
        ↓
clock merging/gating
        ↓
explicit stateful clock bridge
        ↓
multicycle feedback computation
        ↓
HOLD / ZERO / VALID outputs
```

and satisfies all of the following:

1. every logical flow has an explicit clock and occurrence offset;
2. global semantic cursor mutation is no longer fundamental;
3. `.step()` is pure logical reindexing;
4. same-occurrence combinational feedback is rejected by causality analysis;
5. feedback with logical advance derives a physical minimum-separation requirement;
6. inferred clocks can be slowed to meet the requirement;
7. external event clocks are checked against their timing guarantee;
8. throughput violations are distinguished from logical causality errors;
9. every stateful cross-clock information-preserving conversion is explicit in normalized IR;
10. identical bridges are shared;
11. vector bridges remain packed where possible;
12. additive event sources can be merged before accumulation;
13. bridge state can be fused with compatible logical state when possible;
14. exported sparse flows have explicit HOLD/ZERO/VALID materialization;
15. simulation matches a pure Python reference across irregular event schedules.

After this milestone, the autonomous-market event-sampling problem should be solved at the semantic level rather than by a special-purpose workaround, and the compiler will have the temporal substrate required for external-device protocols and drivers.

## Phase 4 semantic-only delivery (complete; reference-only)

The Phase 4 delivery adds `Circuit.sample_on(...)` for raw same-circuit scalar/vector Level inputs and
any same-Circuit declared Event target. Crossings are interned in declaration order and appear in
semantic Event reactions as observations from the normalized Level snapshot at each activation. The
crossing payload shape is always the Level source shape. They have no state, expression, output,
capture, physical lowering, bridge, or blueprint representation.

The reference API accepts explicit HOLD/ZERO/VALID materialization policies for Event and SampleOn
values over a validated half-open timestamp domain. HOLD retains the last present row, ZERO emits the
canonical zero/empty row between occurrences, and VALID pairs rows with a presence flag. These are
post-simulation reference transforms only; they do not promise physical storage, pulse generation,
activation gates, bridges, or Factorio valid wiring.
