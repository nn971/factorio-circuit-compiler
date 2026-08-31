"""Deterministic periodic-state programs for compiler differential tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from random import Random

from factorio_circuit import Circuit, Expr, SignalId, SignalsExpr
from factorio_circuit.target.factorio.semantics import i32

COUNT = SignalId("virtual", "signal-Q")


@dataclass(frozen=True, slots=True)
class StateCondition:
    """One scalar guard used by a generated state transition."""

    source: str
    index: int = 0
    comparator: str = "!="
    right: int = 0


@dataclass(frozen=True, slots=True)
class StateOperation:
    """One ordered periodic update to the generated accumulator."""

    kind: str
    vector_input: int = 0
    scale: int = 1
    condition: StateCondition = StateCondition("always")


@dataclass(frozen=True, slots=True)
class PeriodicStateProgram:
    """Serializable single-clock periodic state program."""

    seed: int
    vector_input_count: int
    scalar_input_count: int
    operations: tuple[StateOperation, ...]
    outputs: tuple[str, ...]

    def describe(self) -> str:
        lines = [
            f"seed={self.seed} vector_inputs={self.vector_input_count} "
            f"scalar_inputs={self.scalar_input_count}"
        ]
        for index, operation in enumerate(self.operations):
            condition = operation.condition
            guard = (
                "always"
                if condition.source == "always"
                else f"{condition.source}{condition.index} {condition.comparator} {condition.right}"
            )
            if operation.kind == "add":
                lines.append(
                    f"op{index}: add vec{operation.vector_input} * {operation.scale} when {guard}"
                )
            else:
                lines.append(f"op{index}: clear when {guard}")
        lines.append("outputs = " + ", ".join(self.outputs))
        return "\n".join(lines)


_SIGNAL_CATALOG = (
    COUNT,
    SignalId("item", "iron-plate"),
    SignalId("item", "copper-plate"),
    SignalId("item", "coal"),
    SignalId("item", "stone"),
)
_INTERESTING_VALUES = (0, 1, -1, 2, -2, 7, 31, 2**31 - 1, -(2**31))
_COMPARATORS = ("==", "!=", "<", "<=", ">", ">=")
_THRESHOLDS = (-2, -1, 0, 1, 2, 7)
_SCALES = (-2, -1, 1, 2)
_OUTPUTS = ("state", "count", "positive")


def generate_periodic_state_program(
    seed: int,
    *,
    min_vector_inputs: int = 1,
    max_vector_inputs: int = 2,
    min_scalar_inputs: int = 1,
    max_scalar_inputs: int = 2,
    min_operations: int = 2,
    max_operations: int = 4,
) -> PeriodicStateProgram:
    """Generate one ordered accumulator program with an inferred uniform physical period."""

    rng = Random(seed)
    vector_input_count = rng.randint(min_vector_inputs, max_vector_inputs)
    scalar_input_count = rng.randint(min_scalar_inputs, max_scalar_inputs)
    operation_count = rng.randint(min_operations, max_operations)

    # Force at least one state-dependent transition into every generated program.  This makes G3
    # exercise genuine multi-tick timing rather than degenerating to a period-1 register loop.
    operations: list[StateOperation] = [
        StateOperation(
            "add",
            vector_input=rng.randrange(vector_input_count),
            scale=rng.choice(_SCALES),
            condition=StateCondition(
                "state",
                comparator=rng.choice(("<", "<=", ">", ">=")),
                right=rng.choice(_THRESHOLDS),
            ),
        )
    ]

    for _ in range(operation_count - 1):
        kind = "clear" if rng.random() < 0.3 else "add"
        condition = _random_condition(rng, scalar_input_count)
        if kind == "clear":
            operations.append(StateOperation("clear", condition=condition))
        else:
            operations.append(
                StateOperation(
                    "add",
                    vector_input=rng.randrange(vector_input_count),
                    scale=rng.choice(_SCALES),
                    condition=condition,
                )
            )

    output_count = rng.randint(1, len(_OUTPUTS))
    outputs = tuple(rng.sample(_OUTPUTS, output_count))
    return PeriodicStateProgram(
        seed,
        vector_input_count,
        scalar_input_count,
        tuple(operations),
        outputs,
    )


def build_periodic_state_circuit(program: PeriodicStateProgram) -> Circuit:
    """Elaborate a generated state program through the public symbolic frontend."""

    circuit = Circuit(f"random_periodic_state_{program.seed}")
    vectors = [circuit.signals(f"vec{index}") for index in range(program.vector_input_count)]
    scalars = [circuit.input(f"scalar{index}") for index in range(program.scalar_input_count)]
    memory = circuit.accumulator("memory")

    # State ordering is explicit: all guards observe the old boundary before any transition.  The
    # committed value is observed only after advancing to the next logical boundary below.
    old_state = memory.value
    for operation in program.operations:
        when = _build_condition(old_state, scalars, operation.condition)
        if operation.kind == "clear":
            memory.clear(when)
            continue
        if operation.kind != "add":
            raise ValueError(f"unsupported state operation {operation.kind!r}")
        value = vectors[operation.vector_input]
        if operation.scale != 1:
            value = value * operation.scale
        memory.add(value, when=when)

    circuit.step(1)
    state = memory.value
    for output in program.outputs:
        if output == "state":
            circuit.output("state", state)
        elif output == "count":
            circuit.output("count", state.signal(COUNT))
        elif output == "positive":
            circuit.output("positive", state.positive())
        else:
            raise ValueError(f"unsupported state output {output!r}")
    return circuit


def generate_periodic_state_input_stream(
    program: PeriodicStateProgram,
    *,
    seed: int,
    cases: int = 12,
) -> list[dict[str, object]]:
    """Generate deterministic sparse vector inputs and signed-32-bit control traces."""

    rng = Random(seed)
    stream: list[dict[str, object]] = []
    for _ in range(cases):
        row: dict[str, object] = {}
        for input_index in range(program.vector_input_count):
            signals: dict[SignalId, int] = {}
            for signal in _SIGNAL_CATALOG:
                if rng.random() >= 0.5:
                    continue
                value = _random_i32(rng)
                if value != 0:
                    signals[signal] = value
            row[f"vec{input_index}"] = signals
        for input_index in range(program.scalar_input_count):
            row[f"scalar{input_index}"] = _random_i32(rng)
        stream.append(row)
    return stream


def shrink_periodic_state_program(
    program: PeriodicStateProgram,
    fails: Callable[[PeriodicStateProgram], bool],
) -> PeriodicStateProgram:
    """Greedily reduce outputs, ordered state updates, guards, and add scales."""

    current = program
    changed = True
    while changed:
        changed = False

        if len(current.outputs) > 1:
            for output in current.outputs:
                candidate = replace(current, outputs=(output,))
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                continue

        if len(current.operations) > 1:
            for index in range(len(current.operations)):
                candidate = replace(
                    current,
                    operations=current.operations[:index] + current.operations[index + 1 :],
                )
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                continue

        for index, operation in enumerate(current.operations):
            if operation.condition.source != "always":
                candidate_operation = replace(operation, condition=StateCondition("always"))
                candidate = _replace_operation(current, index, candidate_operation)
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
            if operation.kind == "add" and operation.scale != 1:
                candidate = _replace_operation(current, index, replace(operation, scale=1))
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
        if changed:
            continue

    return current


def _replace_operation(
    program: PeriodicStateProgram,
    index: int,
    operation: StateOperation,
) -> PeriodicStateProgram:
    operations = list(program.operations)
    operations[index] = operation
    return replace(program, operations=tuple(operations))


def _random_condition(rng: Random, scalar_input_count: int) -> StateCondition:
    roll = rng.random()
    if roll < 0.2:
        return StateCondition("always")
    if roll < 0.65:
        return StateCondition(
            "scalar",
            index=rng.randrange(scalar_input_count),
            comparator=rng.choice(_COMPARATORS),
            right=rng.choice(_THRESHOLDS),
        )
    return StateCondition(
        "state",
        comparator=rng.choice(_COMPARATORS),
        right=rng.choice(_THRESHOLDS),
    )


def _build_condition(
    old_state: SignalsExpr,
    scalars: list[Expr],
    condition: StateCondition,
) -> Expr | int:
    if condition.source == "always":
        return 1
    if condition.source == "scalar":
        value = scalars[condition.index]
    elif condition.source == "state":
        value = old_state.signal(COUNT)
    else:
        raise ValueError(f"unsupported condition source {condition.source!r}")
    return _compare(value, condition.comparator, condition.right)


def _compare(left: Expr, comparator: str, right: int) -> Expr:
    if comparator == "==":
        return left == right
    if comparator == "!=":
        return left != right
    if comparator == "<":
        return left < right
    if comparator == "<=":
        return left <= right
    if comparator == ">":
        return left > right
    if comparator == ">=":
        return left >= right
    raise ValueError(f"unsupported comparator {comparator!r}")


def _random_i32(rng: Random) -> int:
    if rng.random() < 0.45:
        return rng.choice(_INTERESTING_VALUES)
    return i32(rng.getrandbits(32))
