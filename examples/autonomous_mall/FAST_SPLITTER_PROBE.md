# Fast-splitter closed-loop mall probe

This is the first autonomous feedback test above the manually validated seamed worker pool. It is
intentionally one recipe only. The purpose is to validate the loop

```text
real logistic stock -> target comparison -> four-phase offer -> worker -> assembler -> provider
        ^                                                               |
        +---------------------------------------------------------------+
```

before introducing a recipe ROM or recursive scheduler.

## Generate

Use the probe branch and generate one worker first:

```bash
git fetch origin
git switch agent/fast-splitter-closed-loop-probe
git pull

uv run python -m examples.autonomous_mall.fast_splitter_probe \
  --workers 1 > fast-splitter-probe.txt
```

Import the resulting blueprint string into Factorio.

## Recipe under test

The controller publishes exactly one craft of the vanilla `fast-splitter` recipe:

```text
recipe signal: recipe:fast-splitter = 1

inputs:
    splitter            = 1
    iron-gear-wheel     = 10
    electronic-circuit  = 10

product:
    fast-splitter       = 1
```

The packet is fixed and continuously present on the internal controller-to-HEAD interface. Only the
four-phase `offer_valid` state changes.

## External control seam

The surviving top seam is ordered left-to-right:

```text
1  inventory          GREEN vector INPUT
2  offer_valid        GREEN signal-V OUTPUT
3  blocked            RED   signal-B OUTPUT
4  accepted           RED   signal-A OUTPUT
5  busy_count         RED   signal-C OUTPUT
6  completion_count   RED   signal-D OUTPUT
7  reserved           RED   vector OUTPUT
8  promised           RED   vector OUTPUT
9  settling           GREEN signal-Z OUTPUT
```

Only `inventory` is required to operate the controller. The other eight docks are diagnostics.

## Wire the real stock feedback

1. Put the worker, roboport, logistic robots, and ingredient provider/storage chests in one logistic
   network.
2. Configure the roboport to **Read logistic network contents**.
3. Connect the roboport to the `inventory` dock with a green wire.
4. Connect a constant combinator to the same green network and set `signal-T` to the desired number
   of fast splitters. For the first test, use `T = 5`.
5. Make at least the following ingredients available through the logistic network:

```text
splitter            >= 5
iron-gear-wheel     >= 50
electronic-circuit  >= 50
```

Do not place fast splitters in the network initially. Do not manually drive `offer_valid`; the new
controller owns the entire four-phase transaction.

## Expected behavior

With `T = 5`, the system should repeatedly execute this sequence without manual intervention:

```text
inventory says fast-splitter < 5
and worker idle
and one craft of ingredients is visible
        |
        v
offer_valid rises
        |
        v
HEAD/worker accepts exactly one craft
        |
        v
offer_valid returns low and completes its four-phase re-arm
        |
        v
worker crafts one fast splitter
        |
        v
worker becomes idle
        |
        v
controller remains in settling state until roboport stock increases
        |
        v
next craft may be issued
```

The target-boundary invariant is the important part: when the logistic network reaches exactly five
fast splitters, no sixth craft should be accepted.

During each active craft the diagnostics should still match the worker-pool contract:

```text
busy_count = 1
reserved:
    splitter = 1
    iron-gear-wheel = 10
    electronic-circuit = 10
promised:
    fast-splitter = 1
```

`settling` (`signal-Z`) stays high after acceptance until the worker is idle and the roboport reports
a fast-splitter count greater than the stock snapshot taken at that acceptance. This deliberately
bridges the short interval in which the worker promise can clear before the output provider is
visible through the logistic network.

## First-test assumptions

This probe is deliberately stricter than the future mall controller:

- it issues only when the worker pool is idle, so there is no ingredient overcommit problem yet;
- it expects all three fast-splitter ingredients to be supplied externally;
- it assumes fast splitters are not consumed while one craft is in the `settling` state;
- it uses one worker first even though the underlying seamed pool supports more workers.

Once this converges exactly in-game, the next useful extension is to remove the idle-only restriction
and replace the single-product settlement snapshot with explicit stock/reservation/promise escrow.
Only after that should the one-recipe constants be generalized into a vector ROM for multiple
recipes.
