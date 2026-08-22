# Temporal alignment contract

Physical lowering must distinguish *why* a value is requested at a later physical phase. These
operations are not interchangeable even when they return a value carrying the same semantic payload.

## Level reuse

A periodic Level value may already be proved stable across an interval. Reusing that physical
representation at a later phase requires no combinator and does not create a new temporal token.

`LevelAlignmentLowerer.delay_to()` uses this case when the validity window contains the requested
phase.

## Exact transport

Exact transport preserves one already-chosen token while moving it forward in physical time. It is
implemented with identity combinators (or an equivalent transport implementation) and must never be
silently replaced by later observation of a live source.

Use:

- `exact_delay_to(value, phase)` for scalar tokens;
- `exact_delay_vector_to(value, phase)` for vector tokens.

These methods bypass settling reuse, live-source observation, and experimental temporal-plan bus
selection. Shared prefixes of the *same* exact vector token remain a legal implementation
optimization because they do not alter observation time.

## Late observation

A live external Level wire may be observed at a later physical phase without preserving the value
that happened to be present earlier. This is a change of observation boundary, not a delay.

Use:

- `observe_scalar_at(value, phase)`;
- `observe_vector_at(value, phase)`.

`SamplingPolicy.ALAP` permits the compatibility `delay_to()` / `delay_vector_to()` entry points to
choose this operation for phase-zero external Level sources.

A coherent live observation followed by later use is therefore explicitly a composition:

```text
live source --observe at t--> chosen token --exact transport--> consumer
```

## HOLD and pulses

Stateful HOLD is not exact transport. A HOLD captures a token and keeps a representation available
across an interval; periodic Level output materialization already uses explicit capture/feedback
cells for this purpose.

Event/pulse physical lowering is currently outside the supported abstract-physical lowering scope.
When it is added, pulse transport and pulse HOLD must receive their own operations rather than being
folded into `delay_to()`.

## Compatibility `delay_to()`

`delay_to()` remains for existing Level-lowering call sites. In the production lowering chain its
job is deliberately narrow:

1. reuse an already-valid Level representation when possible;
2. otherwise perform exact transport;
3. at the sampling layer, optionally choose late observation for a live external Level source;
4. at an experimental temporal-plan layer, optionally choose a separately specified transport plan.

Code that *requires* preservation of a particular token must not call `delay_to()` and hope the
current policy chooses a physical delay. It must call the explicit exact-transport operation.

This contract is intentionally independent of the future abstract lane/carrier representation and
of any future shared delay-bus implementation.
