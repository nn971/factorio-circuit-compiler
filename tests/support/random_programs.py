"""Deterministic random-program support for compiler differential tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from random import Random

from factorio_circuit import Circuit, Expr


@dataclass(frozen=True, slots=True)
class ScalarRef:
    """Reference an input or earlier node by absolute value index."""

    index: int


ScalarOperand = ScalarRef | int


@dataclass(frozen=True, slots=True)
class ScalarNode:
    """One scalar DAG node in a generated program."""

    op: str
    args: tuple[ScalarOperand, ...]


@dataclass(frozen=True, slots=True)
class ScalarProgram:
    """Serializable description of one generated scalar combinational circuit."""

    seed: int
    input_count: int
    nodes: tuple[ScalarNode, ...]
    outputs: tuple[ScalarRef, ...]

    def describe(self) -> str:
        lines = [f"seed={self.seed} inputs={self.input_count}"]
        for index, node in enumerate(self.nodes, start=self.input_count):
            rendered = ", ".join(_format_operand(arg) for arg in node.args)
            lines.append(f"v{index} = {node.op}({rendered})")
        lines.append("outputs = " + ", ".join(_format_operand(ref) for ref in self.outputs))
        return "\n".join(lines)


_BINARY_OPS = ("+", "-", "*", "&", "|", "^")
_COMPARE_OPS = ("==", "!=", "<", "<=", ">", ">=")
_ALL_OPS = _BINARY_OPS + _COMPARE_OPS + ("select",)
_INTERESTING_CONSTANTS = (0, 1, -1, 2, -2, 3, 7, 31, 2**31 - 1, -(2**31))


def generate_scalar_program(
    seed: int,
    *,
    min_inputs: int = 2,
    max_inputs: int = 4,
    min_nodes: int = 8,
    max_nodes: int = 12,
) -> ScalarProgram:
    """Generate a small acyclic scalar program from a conservative supported op subset."""

    rng = Random(seed)
    input_count = rng.randint(min_inputs, max_inputs)
    node_count = rng.randint(min_nodes, max_nodes)
    nodes: list[ScalarNode] = []

    for node_offset in range(node_count):
        available_count = input_count + node_offset
        op = rng.choice(_ALL_OPS)
        if op == "select":
            args = (
                _random_ref(rng, available_count),
                _random_operand(rng, available_count),
                _random_operand(rng, available_count),
            )
        else:
            args = (
                _random_ref(rng, available_count),
                _random_operand(rng, available_count),
            )
        nodes.append(ScalarNode(op, args))

    newest = ScalarRef(input_count + node_count - 1)
    output_count = rng.randint(1, min(3, node_count))
    extra_outputs = tuple(
        _random_ref(rng, input_count + node_count) for _ in range(output_count - 1)
    )
    outputs = (newest, *extra_outputs)
    return prune_unreachable(ScalarProgram(seed, input_count, tuple(nodes), outputs))


def build_scalar_circuit(program: ScalarProgram) -> Circuit:
    """Elaborate a generated program through the public symbolic frontend."""

    circuit = Circuit(f"random_scalar_{program.seed}")
    values: list[Expr] = [circuit.input(f"in{index}") for index in range(program.input_count)]

    for node in program.nodes:
        args = tuple(_resolve_operand(values, arg) for arg in node.args)
        values.append(_apply_node(node.op, args))

    for index, output in enumerate(program.outputs):
        circuit.output(f"out{index}", values[output.index])
    return circuit


def prune_unreachable(program: ScalarProgram) -> ScalarProgram:
    """Drop DAG nodes not reachable from outputs and compact absolute references."""

    reachable: set[int] = set()

    def visit(ref: ScalarRef) -> None:
        if ref.index < program.input_count or ref.index in reachable:
            return
        node_index = ref.index - program.input_count
        if node_index < 0 or node_index >= len(program.nodes):
            raise ValueError(f"invalid scalar ref v{ref.index}")
        reachable.add(ref.index)
        for operand in program.nodes[node_index].args:
            if isinstance(operand, ScalarRef):
                visit(operand)

    for output in program.outputs:
        visit(output)

    kept_absolute = [
        program.input_count + index
        for index in range(len(program.nodes))
        if program.input_count + index in reachable
    ]
    remap = {
        old_index: program.input_count + new_offset
        for new_offset, old_index in enumerate(kept_absolute)
    }

    def remap_operand(operand: ScalarOperand) -> ScalarOperand:
        if not isinstance(operand, ScalarRef):
            return operand
        if operand.index < program.input_count:
            return operand
        return ScalarRef(remap[operand.index])

    nodes = tuple(
        ScalarNode(
            program.nodes[old_index - program.input_count].op,
            tuple(
                remap_operand(arg) for arg in program.nodes[old_index - program.input_count].args
            ),
        )
        for old_index in kept_absolute
    )
    outputs = tuple(remap_operand(output) for output in program.outputs)
    assert all(isinstance(output, ScalarRef) for output in outputs)
    return ScalarProgram(program.seed, program.input_count, nodes, outputs)  # type: ignore[arg-type]


def shrink_scalar_program(
    program: ScalarProgram,
    fails: Callable[[ScalarProgram], bool],
) -> ScalarProgram:
    """Greedily minimize a failing DAG by dropping outputs and bypassing expression nodes."""

    current = prune_unreachable(program)
    changed = True
    while changed:
        changed = False

        if len(current.outputs) > 1:
            for output in current.outputs:
                candidate = prune_unreachable(
                    ScalarProgram(current.seed, current.input_count, current.nodes, (output,))
                )
                if fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                continue

        for node_offset, node in enumerate(current.nodes):
            target = ScalarRef(current.input_count + node_offset)
            replacements = tuple(arg for arg in node.args if isinstance(arg, ScalarRef))
            for replacement in replacements:
                candidate = _replace_ref(current, target, replacement)
                if candidate != current and fails(candidate):
                    current = candidate
                    changed = True
                    break
            if changed:
                break

    return current


def _replace_ref(
    program: ScalarProgram,
    target: ScalarRef,
    replacement: ScalarRef,
) -> ScalarProgram:
    def replace_operand(operand: ScalarOperand) -> ScalarOperand:
        return replacement if operand == target else operand

    nodes = tuple(
        ScalarNode(node.op, tuple(replace_operand(arg) for arg in node.args))
        for node in program.nodes
    )
    outputs = tuple(replacement if output == target else output for output in program.outputs)
    return prune_unreachable(ScalarProgram(program.seed, program.input_count, nodes, outputs))


def _random_ref(rng: Random, available_count: int) -> ScalarRef:
    recent_start = max(0, available_count - 4)
    if available_count > 4 and rng.random() < 0.7:
        return ScalarRef(rng.randrange(recent_start, available_count))
    return ScalarRef(rng.randrange(available_count))


def _random_operand(rng: Random, available_count: int) -> ScalarOperand:
    if rng.random() < 0.35:
        return rng.choice(_INTERESTING_CONSTANTS)
    return _random_ref(rng, available_count)


def _resolve_operand(values: list[Expr], operand: ScalarOperand) -> Expr | int:
    return values[operand.index] if isinstance(operand, ScalarRef) else operand


def _apply_node(op: str, args: tuple[Expr | int, ...]) -> Expr:
    left = args[0]
    if not isinstance(left, Expr):
        raise ValueError(f"{op} requires a symbolic first operand")

    if op == "select":
        return left.select(args[1], args[2])
    right = args[1]
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "&":
        return left & right
    if op == "|":
        return left | right
    if op == "^":
        return left ^ right
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    raise ValueError(f"unsupported scalar op {op!r}")


def _format_operand(operand: ScalarOperand) -> str:
    return f"v{operand.index}" if isinstance(operand, ScalarRef) else repr(operand)
