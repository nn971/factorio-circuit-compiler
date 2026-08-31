# Train-stop command/status interface

Milestone F4 adds `TrainStopDevice`, a reusable vanilla Factorio train-stop boundary that composes through the same rigid-provider path as other external devices.

## Typed protocol

The device deliberately exposes two electrically separated persistent vector buses:

| Port | Direction | Modality | Shape | Wire | Meaning |
| --- | --- | --- | --- | --- | --- |
| `commands` | input | Level | vector | green | signals sent to a stopped train plus station control lanes |
| `status` | output | Level | vector | red | stopped-train contents plus station status lanes |

The GREEN/RED split is part of the ABI. A train stop can both consume and emit circuit signals; keeping those functions on different electrical networks prevents its own status from feeding back into command interpretation.

## Reserved virtual signals

The reusable device follows the base-game train-stop conventions:

- `signal-L` on `commands`: circuit-controlled train limit;
- `signal-P` on `commands`: circuit-controlled stop priority;
- `signal-T` on `status`: stopped-train identifier;
- `signal-C` on `status`: incoming train count.

Other signals on `commands` are sent to a train currently stopped at the station. Other signals on `status` are the stopped train's circuit-readable contents.

`signal-T` and `signal-C` are declared as fixed lanes by the status oracle provider before synthesis. The open status vector therefore remains available for item/fluid contents without allowing the compiler's scalar signal allocator to reuse those metadata identities accidentally. `signal-L` and `signal-P` are caller-owned command conventions rather than outputs of the provider.

## Physical device

The standalone blueprint contains three entities:

1. a GREEN constant-combinator dock for `commands`;
2. a RED constant-combinator dock for `status`;
3. the real `train-stop` entity.

The stop is configured to:

- send GREEN circuit signals to the stopped train;
- read stopped-train contents onto RED;
- emit the stopped-train id as `signal-T`;
- emit incoming train count as `signal-C`;
- read the circuit train limit from `signal-L`;
- read station priority from `signal-P`.

The docks are stable typed attachment points. The train stop itself remains an opaque Factorio entity whose exact control behavior and blueprint payload survive provider composition and final serialization.

## Compiler integration

`examples/train_stop_device_probe.py` demonstrates the intended composition pattern. The semantic program declares an ordinary deterministic vector input:

```python
commands = circuit.signals("commands")
```

and a vector oracle:

```python
status = circuit.oracle_signals("status")
circuit.bind_oracle_input(status, "commands", commands)
```

The target provider binds both buses to one `TrainStopDevice` rigid component. The hidden provider-input marker for `commands` and the temporary device-port proxies are construction details and do not survive final placement. The final blueprint retains `commands` as a public GREEN input while the RED `status` oracle is supplied by the actual train stop.

Generate the standalone device with:

```bash
uv run python -m factorio_circuit.devices.train_stop
```

Generate the integrated compiler probe with:

```bash
uv run python examples/train_stop_device_probe.py
```

## Scope

This interface is intentionally a low-level train-stop peripheral, not a dispatch algorithm. Scheduling, provider/requester matching, hysteresis, reservation accounting, train composition policy, and depot logic belong in higher-level compiled programs. This keeps the reusable device useful for decentralized controllers as well as centralized train systems.
