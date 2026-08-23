# Fast-splitter raw-plate closed-loop mall probe

This is the first autonomous recursive-production test above the manually validated seamed worker
pool. The only prescribed source materials are **iron plates and copper plates**. The controller
manufactures the vanilla intermediates itself and repeatedly produces fast splitters until the
configured final-stock target is reached.

```text
real logistic stock -> dependency choice -> four-phase offer -> worker -> assembler -> provider
        ^                                                                  |
        +------------------------------------------------------------------+
```

This remains a target-specific probe. Its purpose is to validate real recursive feedback before the
recipe decisions are generalized into the constant-combinator vector ROM used by the final mall.

## Generate

```bash
git fetch origin
git switch agent/fast-splitter-closed-loop-probe
git pull

uv run python -m examples.autonomous_mall.fast_splitter_probe \
  --workers 1 > fast-splitter-probe.txt
```

Import the resulting blueprint string into Factorio.

## Recipe chain under test

The controller may issue exactly one craft at a time from this vanilla chain:

```text
copper-plate
  -> copper-cable
  -> electronic-circuit ---+
                            |
iron-plate -> iron-gear-wheel -> transport-belt ---+
                            |                       |
                            +--------------------> splitter
                                                    |
                                                    +--> fast-splitter
```

Concrete one-craft packets:

```text
copper-cable:
    input   copper-plate = 1
    product copper-cable = 2

electronic-circuit:
    inputs  copper-cable = 3, iron-plate = 1
    product electronic-circuit = 1

iron-gear-wheel:
    input   iron-plate = 2
    product iron-gear-wheel = 1

transport-belt:
    inputs  iron-plate = 1, iron-gear-wheel = 1
    product transport-belt = 2

splitter:
    inputs  transport-belt = 4, electronic-circuit = 5, iron-plate = 5
    product splitter = 1

fast-splitter:
    inputs  splitter = 1, iron-gear-wheel = 10, electronic-circuit = 10
    product fast-splitter = 1
```

Each packet uses the corresponding explicit Factorio `recipe:<name>` signal.

With no useful intermediates initially stocked, one fast splitter consumes **46 iron plates** and
22.5 copper plates in continuous raw-material accounting. Because the copper-cable recipe produces
two at a time, a clean one-fast-splitter run needs **46 iron plates + 23 copper plates** and leaves
one copper cable. Across several targets, leftover cable is reused; five fast splitters from an
otherwise empty network therefore need 230 iron plates and 113 copper plates.

## External control seam

The surviving top seam is ordered left-to-right:

```text
1   inventory          GREEN vector INPUT
2   offer_valid        GREEN signal-V OUTPUT
3   blocked            RED   signal-B OUTPUT
4   accepted           RED   signal-A OUTPUT
5   busy_count         RED   signal-C OUTPUT
6   completion_count   RED   signal-D OUTPUT
7   reserved           RED   vector OUTPUT
8   promised           RED   vector OUTPUT
9   settling           GREEN signal-Z OUTPUT
10  job_recipe         GREEN recipe-vector OUTPUT
```

Only `inventory` is required to operate the controller. The remaining docks are diagnostics;
`job_recipe` makes the current recursive choice visible during debugging.

## Wire the real stock feedback

1. Put the worker, roboport, logistic robots, raw-material provider/storage chests, requester chest,
   and output provider in one logistic network.
2. Configure the roboport to **Read logistic network contents**.
3. Connect the roboport to the `inventory` dock with a green wire.
4. Connect a constant combinator to that same green network and set `signal-T` to the desired number
   of fast splitters.
5. Supply only iron plates and copper plates through the logistic network. You do **not** need to
   pre-supply splitters, gears, circuits, belts, or copper cable.

For the smallest test use:

```text
signal-T = 1
iron-plate >= 46
copper-plate >= 23
fast-splitter = 0
```

For a five-item convergence test from an otherwise empty intermediate stock:

```text
signal-T = 5
iron-plate >= 230
copper-plate >= 113
fast-splitter = 0
```

Do not manually drive `offer_valid`, recipe, input, or product lanes. The controller owns the entire
four-phase transaction.

## Expected behavior

For `T = 1`, the visible `job_recipe` diagnostic should walk through the required intermediates. The
exact interleaving contains repeated cable/circuit/gear crafts, but the dependency phases are:

```text
copper-cable / electronic-circuit
        -> iron-gear-wheel / transport-belt
        -> splitter
        -> iron-gear-wheel as needed for the final recipe
        -> fast-splitter
        -> stop at stock = 1
```

The controller recomputes the next needed craft from actual roboport stock after every completed
craft. Existing useful intermediates are therefore consumed if present rather than ignored.

## Settlement and the previous one-craft deadlock

A physical `accepted` response can remain high for several logical reactions. The first version took
its stock baseline on **every** high reaction. If the produced item became visible while `accepted`
was still high, the baseline moved up to the new stock and the controller could wait forever for a
second increase. This is why an in-game `T = 5` run stopped after one fast splitter.

The corrected controller uses only the **first accepted observation** for each transaction:

```text
first accepted
    -> snapshot stock once
    -> remember promised product
    -> wait for worker idle + that product stock > snapshot
    -> clear settling
    -> choose next dependency craft
```

This same product-specific barrier is used for copper cable, circuits, gears, belts, splitters, and
fast splitters.

## Diagnostics

During any active craft:

```text
busy_count = 1
reserved   = exact inputs of current one-craft recipe
promised   = exact products of current one-craft recipe
job_recipe = explicit recipe signal for that craft
```

`settling` (`signal-Z`) remains high after acceptance until the worker is idle and the relevant
product count has increased in roboport-visible logistic stock.

## First-test assumptions

This probe deliberately keeps the hard parts separated:

- use one worker first;
- only one craft is in flight at a time;
- iron plates and copper plates are the prescribed external/raw inputs;
- useful existing intermediates may be consumed;
- do not externally consume the current craft's product while `settling` is high.

Once this raw-plate chain converges correctly in-game, the next step is to replace these hard-coded
recipe choices with the general constant-combinator vector ROM and scheduler rather than adding more
target-specific recipes to this file.
