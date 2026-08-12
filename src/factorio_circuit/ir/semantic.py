"""Logical circuit IR: timed scalar dataflow plus whole-vector state boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_circuit.ir.physical import SignalId
from factorio_circuit.ir.state import StateOperation, StateRegister, VectorRegisterRead


@dataclass(frozen=True, slots=True)
class Input:
    """A scalar external input stream at the invocation's base logical tick."""

    name: str


@dataclass(frozen=True, slots=True)
class InputSample:
    """A fresh observation of ``source`` at ``t + offset``."""

    source: Input
    offset: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class VectorInput:
    """A complete Factorio signal-map input port at the base logical tick."""

    name: str


@dataclass(frozen=True, slots=True)
class VectorInputSample:
    """A fresh whole-vector observation of ``source`` at ``t + offset``."""

    source: VectorInput
    offset: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class VectorConstant:
    """A constant whole-vector stream."""

    signals: tuple[tuple[SignalId, int], ...]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Constant:
    value: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class BinaryOp:
    op: str
    left: ScalarValue
    right: ScalarValue
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Compare:
    op: str
    left: ScalarValue
    right: ScalarValue
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Select:
    """Select ``when_true`` when ``condition != 0``, otherwise ``when_false``."""

    condition: ScalarValue
    when_true: ScalarValue
    when_false: ScalarValue
    name: str | None = None


@dataclass(frozen=True, slots=True)
class VectorSignal:
    """Read one concrete signal lane from a whole-vector stream."""

    vector: VectorValue
    signal: SignalId
    name: str | None = None


ScalarValue = Input | InputSample | Constant | BinaryOp | Compare | Select | VectorSignal
VectorValue = VectorInput | VectorInputSample | VectorConstant | VectorRegisterRead
Value = ScalarValue
OutputValue = ScalarValue | VectorValue
DerivedValue = BinaryOp | Compare | Select


@dataclass(frozen=True, slots=True)
class ReturnValue:
    values: tuple[OutputValue, ...]
    names: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        if self.names and len(self.names) != len(self.values):
            raise ValueError("output names must match output values")


@dataclass(frozen=True, slots=True)
class CircuitModule:
    name: str
    inputs: tuple[Input, ...]
    operations: tuple[DerivedValue, ...]
    output: ReturnValue
    vector_inputs: tuple[VectorInput, ...] = ()
    state_registers: tuple[StateRegister, ...] = ()
    state_operations: tuple[StateOperation, ...] = ()


def dependencies(value: ScalarValue) -> tuple[ScalarValue, ...]:
    if isinstance(value, (Input, InputSample, Constant, VectorSignal)):
        return ()
    if isinstance(value, (BinaryOp, Compare)):
        return (value.left, value.right)
    if isinstance(value, Select):
        return (value.condition, value.when_true, value.when_false)
    raise TypeError(value)


def reachable_operations(module: CircuitModule) -> tuple[DerivedValue, ...]:
    """Return scalar stateless operations reachable from scalar outputs/state controls."""

    result: list[DerivedValue] = []
    seen: set[int] = set()

    def visit(value: ScalarValue) -> None:
        key = id(value)
        if key in seen:
            return
        seen.add(key)
        for child in dependencies(value):
            visit(child)
        if isinstance(value, (BinaryOp, Compare, Select)):
            result.append(value)

    for output in module.output.values:
        if isinstance(
            output, (Input, InputSample, Constant, BinaryOp, Compare, Select, VectorSignal)
        ):
            visit(output)
    from factorio_circuit.ir.state import AccumulatorAdd, AccumulatorClear, FreezeSet

    for op in module.state_operations:
        if isinstance(op, (AccumulatorAdd, AccumulatorClear, FreezeSet)):
            visit(op.when)
    return tuple(result)
