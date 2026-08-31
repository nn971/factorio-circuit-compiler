"""Deterministic vector-program support for compiler differential tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from random import Random

from factorio_circuit import SignalId
from factorio_circuit.frontend import Circuit, SignalsExpr
from factorio_circuit.target.factorio.semantics import i32


@dataclass(frozen=True, slots=True)
class VectorRef:
    """Reference a vector input or earlier vector node by absolute value index."""

    index: int


@dataclass(frozen=True, slots=True)
class ScalarInputRef:
    """Reference one scalar control input."""

    index: int


VectorOperand = VectorRef | ScalarInputRef | int


@dataclass(frozen=True, slots=True)
class VectorNode:
    """One vector DAG node in a generated program."""

    op: str
    args: tuple[VectorOperand, ...]


@dataclass(frozen=True, slots=True)
class VectorProgram:
    """Serializable description of one generated vector combinational circuit."""

    seed: int
    vector_input_count: int
    scalar_input_count: int
    nodes: tuple[VectorNode, ...]
    outputs: tuple[VectorRef, ...]

    def describe(self) -> str:
        lines = [
            f"seed={self.seed} vector_inputs={self.vector_input_count} "
            f"scalar_inputs={self.scalar_input_count}"
        ]
        for index, node in enumerate(self.nodes, start=self.vector_input_count):
            rendered = ", ".join(_format_operand(arg) for arg in node.args)
            lines.append(f"v{index} = {node.op}({rendered})")
        lines.append("outputs = " + ", ".join(_format_operand(ref) for ref in self.outputs))
        return "\n".join(lines)


_SIGNAL_CATALOG = (
    SignalId("item", "iron-plate"),
    SignalId("item", "copper-plate"),
    SignalId("item", "coal"),
    SignalId("item", "stone"),
    SignalId("item", "steel-plate"),
)
_VECTOR_OPS = (
    "add",
    "sub",
    "mul",
    "neg",
    "gate",
    "filter_eq",
    "filter_ne",
    "filter_lt",
    "filter_le",
    "filter_gt",
    "filter_ge",
    "select",
)
_INTERESTING_VALUES = (0, 1, -1, 2, -2, 7, 31, 2**31 - 1, -(2**31))
_FILTER_CONSTANTS = (-2, -1, 0, 1, 2, 7)


def generate_vector_program(
    seed: int,
    *,
    min_vector_inputs: int = 2,
    max_vector_inputs: int = 3,
    min_scalar_inputs: int = 1,
    max_scalar_inputs: int = 2,
    min_nodes: int = 7,
    max_nodes: int = 10,
) -> VectorProgram:
    """Generate a small acyclic runtime-open vector program."""

    rng = Random(seed)
    vector_input_count = rng.randint(min_vector_inputs, max_vector_inputs)
    scalar_input_count = rng.randint(min_scalar_inputs, max_scalar_inputs)
    node_count = rng.randint(min_nodes, max_nodes)
    nodes: list[VectorNode] = []

    for node_offset in range(node_count):
        available_vectors = vector_input_count + node_offset
        source = _random_vector_ref(rng, available_vectors)
        op = rng.choice(_VECTOR_OPS)
        if op in {"add", "sub"}:
            args: tuple[VectorOperand, ...] = (
                source,
                _random_vector_ref(rng, available_vectors),
            )
        elif op in {"mul", "gate"}:
            args = (source, _random_scalar_operand(rng, scalar_input_count))
        elif op == "neg":
            args = (source,)
        elif op == "select":
            args = (source, rng.randrange(3), int(rng.choice((False, True))))
        else:
            args = (source, rng.choice(_FILTER_CONSTANTS))
        nodes.append(VectorNode(op, args))

    newest = VectorRef(vector_input_count + node_count - 1)
    output_count = rng.randint(1, min(3, node_count))
    extra_outputs = tuple(
        _random_vector_ref(rng, vector_input_count + node_count) for _ in range(output_count - 1)
    )
    outputs = (newest, *extra_outputs)
    return prune_unreachable_vectors(
        VectorProgram(seed, vector_input_count, scalar_input_count, tuple(nodes), outputs)
    )


def build_vector_circuit(program: VectorProgram) -> Circuit:
    """Elaborate a generated vector program through the public symbolic frontend."""

    circuit = Circuit(f"random_vector_{program.seed}")
    values: list[SignalsExpr] = [
        circuit.signals(f"vec{index}") for index in range(program.vector_input_count)
    ]
    scalars = [circuit.input(f"scalar{index}") for index in range(program.scalar_input_count)]

    for node in program.nodes:
        source = values[_vector_arg(node, 0).index]
        if node.op == "add":
            value = source + values[_vector_arg(node, 1).index]
        elif node.op == "sub":
            value = source - values[_vector_arg(node, 1).index]
        elif node.op == "mul":
            value = source * _resolve_scalar(scalars, node.args[1])
        elif node.op == "neg":
            value = -source
        elif node.op == "gate":
            value = source.gate(_resolve_scalar(scalars, node.args[1]))
        elif node.op.startswith("filter_"):
            right = _integer_arg(node, 1)
            value = getattr(source, node.op)(right)
        elif node.op == "select":
            value = source.select(
                _integer_arg(node, 1),
                descending=bool(_integer_arg(node, 2)),
            )
        else:  # pragma: no cover - generator owns the operation vocabulary
            raise ValueError(f"unsupported vector op {node.op!r}")
        values.append(value)

    for index, output in enumerate(program.outputs):
        circuit.output(f"out{index}", values[output.index])
    return circuit


def generate_vector_input_stream(
    program: VectorProgram,
    *,
    seed: int,
    cases: int = 18,
) -> list[dict[str, object]]:
    """Generate deterministic sparse vector/scalar input rows with i32-heavy values."""

    rng = Random(seed)
    stream: list[dict[str, object]] = []
    for _ in range(cases):
        row: dict[str, object] = {}
        for input_index in range(program.vector_input_count):
            signals: dict[SignalId, int] = {}
            for signal in _SIGNAL_CATALOG:
                if rng.random() >= 0.55:
                    continue
                value = _random_i32(rng)
                if value != 0:
                    signals[signal] = value
            row[f"vec{input_index}"] = signals
        for input_index in range(program.scalar_input_count):
            row[f"scalar{input_index}"] = _random_i32(rng)
        stream.append(row)
    return stream


def prune_unreachable_vectors(program: VectorProgram) -> VectorProgram:
    """Drop vector nodes not reachable from outputs and compact vector references."""

    reachable: set[int] = set()

    def visit(ref: VectorRef) -> None:
        if ref.index < program.vector_input_count or ref.index in reachable:
            return
        node_index = ref.index - program.vector_input_count
        if node_index < 0 or node_index >= len(program.nodes):
            raise ValueError(f"invalid vector ref v{ref.index}")
        reachable.add(ref.index)
        for operand in program.nodes[node_index].args:
            if isinstance(operand, VectorRef):
                visit(operand)

    for output in program.outputs:
        visit(output)

    kept_absolute = [
        program.vector_input_count + index
        for index in range(len(program.nodes))
        if program.vector_input_count + index in reachable
    ]
    remap = {
        old_index: program.vector_input_count + new_offset
        for new_offset, old_index in enumerate(kept_absolute)
    }

    def remap_operand(operand: VectorOperand) -> VectorOperand:
        if not isinstance(operand, VectorRef) or operand.index < program.vector_input_count:
            return operand
        return VectorRef(remap[operand.index])

    nodes = tuple(
        VectorNode(
            program.nodes[old_index - program.vector_input_count].op,
            tuple(
                remap_operand(arg)
                for arg in program.nodes[old_index - program.vector_input_count].args
            ),
        )
        for old_index in kept_absolute
    )
    outputs = tuple(VectorRef(remap.get(output.index, output.index)) for output in program.outputs)
    return VectorProgram(
        program.seed,
        program.vector_input_count,
        program.scalar_input_count,
        nodes,
        outputs,
    )


def shrink_vector_program(
    program: VectorProgram,
    fails: Callable[[VectorProgram], bool],
) -> VectorProgram:
    """Greedily minimize a failing vector DAG by dropping outputs and bypassing nodes."""

    current = prune_unreachable_vectors(program)
    changed = True
    while changed:
        changed = False
        if len(current.outputs) > 1:
            for output in current.outputs:
                candidate = prune_unreachable_vectors(
                    VectorProgram(
                        current.seed,
                        current.vector_input_count,
                        current.scalar_input_count,
                        current.nodes,
                        (output,),
                    )
                )
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                continue

        for node_offset, node in enumerate(current.nodes):
            target = VectorRef(current.vector_input_count + node_offset)
            replacements = tuple(arg for arg in node.args if isinstance(arg, VectorRef))
            for replacement in replacements:
                candidate = _replace_vector_ref(current, target, replacement)
                if candidate != current and fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                break
    return current


def _replace_vector_ref(
    program: VectorProgram,
    target: VectorRef,
    replacement: VectorRef,
) -> VectorProgram:
    def replace_operand(operand: VectorOperand) -> VectorOperand:
        return replacement if operand == target else operand

    nodes = tuple(
        VectorNode(node.op, tuple(replace_operand(arg) for arg in node.args))
        for node in program.nodes
    )
    outputs = tuple(replacement if output == target else output for output in program.outputs)
    return prune_unreachable_vectors(
        VectorProgram(
            program.seed,
            program.vector_input_count,
            program.scalar_input_count,
            nodes,
            outputs,
        )
    )


def _random_vector_ref(rng: Random, available_count: int) -> VectorRef:
    recent_start = max(0, available_count - 4)
    if available_count > 4 and rng.random() < 0.7:
        return VectorRef(rng.randrange(recent_start, available_count))
    return VectorRef(rng.randrange(available_count))


def _random_scalar_operand(rng: Random, scalar_input_count: int) -> ScalarInputRef | int:
    if rng.random() < 0.55:
        return ScalarInputRef(rng.randrange(scalar_input_count))
    return rng.choice((-2, -1, 0, 1, 2, 3, 7))


def _random_i32(rng: Random) -> int:
    if rng.random() < 0.45:
        return rng.choice(_INTERESTING_VALUES)
    return i32(rng.getrandbits(32))


def _resolve_scalar(scalars: list[object], operand: VectorOperand) -> object:
    if isinstance(operand, ScalarInputRef):
        return scalars[operand.index]
    if isinstance(operand, int):
        return operand
    raise ValueError("expected scalar input reference or integer")


def _vector_arg(node: VectorNode, index: int) -> VectorRef:
    operand = node.args[index]
    if not isinstance(operand, VectorRef):
        raise ValueError(f"{node.op} argument {index} must be a vector ref")
    return operand


def _integer_arg(node: VectorNode, index: int) -> int:
    operand = node.args[index]
    if isinstance(operand, bool) or not isinstance(operand, int):
        raise ValueError(f"{node.op} argument {index} must be an integer")
    return operand


def _format_operand(operand: VectorOperand) -> str:
    if isinstance(operand, VectorRef):
        return f"v{operand.index}"
    if isinstance(operand, ScalarInputRef):
        return f"s{operand.index}"
    return repr(operand)
