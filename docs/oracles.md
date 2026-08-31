# Semantic oracles and physical providers

An **oracle** is an environment-provided Level or Event value that the deterministic semantic program
may observe. Reference simulation supplies a scripted oracle trace or Event schedule. Physical
compilation instead binds each oracle to an explicit **provider** before joint physical synthesis.

This boundary is intentionally broader than selector combinators. Natural providers include:

- selector-backed random/time/metadata observations;
- inventory or logistic-stock readers;
- belt/inserter item-pulse readers;
- temperature or other world-state sensors;
- machine/train/entity observations;
- generated sensor constructions such as a player-movement detector.

Ordinary inputs remain distinct. `c.input("x")` means the compiled circuit exposes a physical input
port that its environment must wire. `c.oracle("x")` means the program depends on a physical
observation for which compilation must make an explicit provider choice. The same distinction applies
to Event sources: `c.signal_event("e", ...)` exposes an external Event boundary, while
`c.oracle_signal_event("e", ...)` requires a target-side provider.

## Frontend

```python
from factorio_circuit import Circuit

c = Circuit("controller")
stock = c.oracle_signals("stock")
temperature = c.oracle("temperature")
item_passed = c.oracle_signal_event("item_passed", guaranteed_min_separation=1)
```

Scalar and whole-vector Level oracles use the ordinary Level expression surface, including
sampling/reindexing rules. Event oracles use the ordinary clocked Event surface, including direct
Event-clocked state, merging, gating, and explicit clock bridges.

## Deterministic reference simulation

Level oracle values are supplied separately from ordinary inputs:

```python
simulate_stream_with_oracles(
    module,
    input_stream=[{"demand": 10}],
    oracle_stream=[{"temperature": 25, "stock": {...}}],
)
```

Event oracle occurrences use the same external Event schedule machinery as ordinary Event inputs. A
fixed Level trace and Event schedule make the semantic computation deterministic while leaving the
physical provider implementation target-side.

The simulation APIs validate declared oracle names and require the corresponding observations or
schedules, so an environment trace cannot silently drift away from the semantic module.

## Physical provider binding

Physical compilation requires exact provider coverage:

```python
c.compile(
    oracle_providers={
        "temperature": ExternalOracleProvider(),
        "stock": ExternalOracleProvider(),
    }
)
```

`ExternalOracleProvider` is an explicit bridge for Level devices that remain manually wired: it
preserves the oracle boundary as a physical port. A dedicated provider can instead insert target
entities and consume that port.

Level providers run after semantic-to-abstract-physical lowering and before signal allocation,
red/green assignment, placement, and routing. Consequently their entities are jointly synthesized
with all ordinary combinators rather than appended to a finished layout.

`ScalarConstantOracleProvider` and `VectorConstantOracleProvider` are small built-in Level providers
for probes, tests, and fixed target observations. They also serve as examples for implementing new
providers.

### Event oracle providers

`Circuit.oracle_event(...)` and `Circuit.oracle_signal_event(...)` create external Event clocks whose
physical lowering uses the compiler's canonical Event ABI:

```text
<name>          payload
<name>__valid   one-tick occurrence token
```

For a vector Event, `<name>` is an open-vector payload net. `<name>__valid` is a scalar Level lane
whose asserted tick marks the simultaneous payload occurrence. The payload and valid lanes therefore
form one physical boundary and must remain phase-aligned.

Event providers are materialized after the clocked/Event physical lowering has created these two nets.
They receive an `EventOraclePhysicalContext`, which exposes the payload net plus the valid net. A rigid
device provider can bind a typed Event output and a fixed-signal scalar Level valid output directly to
those existing abstract nets with `component_event_output_bindings(...)`.

This keeps Event semantics in the existing clocked IR: providers implement the environment-facing
source while ordinary Event state, `event_merge`, `gate_clock`, `sum_into`, and other temporal logic
continue through the same compiler path. Provider composition then reuses Milestone E's rigid-device
pipeline, so temporary payload/valid markers disappear before final routing and exact opaque device
endpoints take their place.

The F3 belt/inserter readers are the reference implementation. Their native one-tick item pulse is
conditioned through equal one-combinator-tick RED payload and GREEN valid paths, preserving exact
payload/valid phase alignment before the Event reaches compiled logic.

## Provider placement contracts

Provider implementation and provider location are separate concerns. A selector combinator can be
placed freely, while a stock reader, temperature sensor, assembler reader, train stop, or generated
sensor construction may have to remain at a world-relative site.

Every provider entity may therefore declare either:

```python
FreePlacement()
AnchoredPlacement("mall-roboport")
```

Ordinary compiler-generated entities remain implicitly free. Provider entities record their
placement constraint in `AbstractPhysicalCircuit.placement_constraints`, so the requirement survives
abstract lowering without introducing coordinates into deterministic semantics.

An anchored provider stores only a symbolic site name during lowering:

```python
provider = ScalarConstantOracleProvider(
    42,
    placement=AnchoredPlacement("furnace-temperature-sensor"),
)
```

A final blueprint resolves that site through deployment configuration:

```python
c.compile(
    oracle_providers={"temperature": provider},
    physical_anchors={
        "furnace-temperature-sensor": (120.0, -36.0),
    },
)
```

`lower_to_abstract_physical(...)` intentionally permits unresolved symbolic anchors. Final physical
placement requires every `ANCHORED` entity to have a coordinate before placement/routing begins.
Low-level entity-id anchors in `PlacementOptions.anchors` may coexist with symbolic anchors when they
resolve to the same position.

Placement is attached per physical entity rather than per oracle provider. A multi-entity provider
may therefore anchor only its world-facing sensor while leaving helper combinators free for the
ordinary placer to optimize around the fixed site.

## Typed provider physical products

Milestone E1 makes provider materialization an explicit typed boundary. Provider-created ordinary
entities still enter `AbstractPhysicalCircuit` exactly as before, but each is also recorded as a
`ProviderEntityProduct` carrying its placement contract. `lower_to_abstract_physical(...)` returns
all such products in `provider_materialization`.

A provider may instead declare one reusable rigid device as a `ProviderRigidComponentProduct`. The
product carries:

- the existing `ExternalDeviceBlueprint` and its typed ports;
- explicit caller-supplied prototype collision geometry;
- D1 footprint, keepout, adapter-region, access-point, and legal-origin contracts;
- the known-valid internal circuit-wire reach of the imported component;
- bindings from named device ports to abstract physical net ids.

The declaration is validated immediately through the same blueprint importer and rigid-geometry
bridge used by the D4 assembler benchmark. Source collision boxes must fit the declared component
geometry, declared internal wires must fit the component's wire envelope, bound ports must exist,
and the provider context checks port direction, scalar/vector payload shape, and the required temporal
modality for each binding helper.

For Level providers, two context helpers construct electrical bindings without inventing temporary
component entities in the abstract graph:

- `component_output_binding(device, port_name)` binds a compatible device output to the oracle net;
- `component_input_binding(name, device, port_name)` consumes a named deterministic provider-input
  tap and binds that net to a compatible device input.

Event providers use `EventOraclePhysicalContext.component_event_output_bindings(...)` to bind the
payload and valid nets as one synchronized physical Event boundary.

Milestone E2 consumes these rigid products during full `compile()`. The composer rebases the imported
device entities into compiler-global ids, constrains bound abstract nets to the device port's required
wire color, and feeds scalar device signals into the shared DSATUR allocator as fixed/precolored
abstract lanes. Temporary annotation proxies let the ordinary placement/routing machinery reason
about component ports before opaque device entities are inserted; those proxies are discarded before
the final `Layout` is validated or serialized.

After ordinary implementation placement, compiler entities are legalized away from the component's
footprint, keepouts, and reserved adapter regions. Routing is rebuilt with those regions excluded from
relay workspace, then proxy endpoints are replaced by the exact opaque entity/connector ids and the
component's imported internal wires are restored. The resulting mixed artifact is validated through
the D1 component-geometry boundary and encoded with the opaque-aware serializer.

Successful E2 compilation therefore validates the same mixed physical object that is serialized; it
does not append a device to an already-routed blueprint. See `provider-composition.md` for the full
composition sequence and current limitations. In particular, rigid providers currently remain at
their declared geometry during full compilation; D2 automatic origin search is not yet invoked for
provider components.

## Deterministic provider inputs

Some physical providers consume deterministic circuit values and return an observation whose target
implementation stays opaque to semantics. Random selection from a computed candidate set is the first
example. Declare the oracle normally, then bind a named provider input:

```python
choice = c.oracle_signals("choice")
free_pixels = ...

c.bind_oracle_input(choice, "candidates", free_pixels)
```

`bind_oracle_input(...)` is a physical-compilation boundary rather than a semantic dataflow edge. A
normal `c.build()` still exposes only the actual semantic outputs. During `c.compile()`, the compiler
lowers the deterministic expression through its ordinary pipeline, gives the resulting physical net
to the provider, and removes the temporary marker before final signal allocation/layout.

A provider retrieves and consumes the net through `OraclePhysicalContext.consume_input(...)`. This
allows one provider to have several named deterministic inputs without baking those inputs into the
meaning of `Oracle` itself. Rigid providers use `component_input_binding(...)` for the same hidden
net when the eventual consumer is a reusable-device port rather than an abstract combinator
endpoint.

The built-in `RandomSignalOracleProvider` consumes a whole-vector `candidates` input and inserts a
freely placeable selector combinator configured for Random Input mode:

```python
result = c.compile(
    oracle_providers={
        "choice": RandomSignalOracleProvider(
            input_name="candidates",
            update_interval=1,
        ),
    },
)
```

The selector remains entirely target-side. Reference simulation supplies `choice` through the oracle
trace and never evaluates randomness. The deterministic program may additionally validate or latch
the proposal before using it; Snake does this so stale selector output can delay food respawn but can
never place food on an occupied cell.
