"""Grid-snapped, paste-assembled transaction tiles for the autonomous mall prototype.

The manually wired prototype proved the transaction semantics but exposed two physical-composition
problems: independently synthesized modules can choose different red/green colors and different scalar
signal allocations. These tiles therefore add an explicit physical ABI around each compiled circuit.

Horizontal whole-vector buses are normalized through tiny EACH*1 adapters onto a red external dock.
Machine scalar docks additionally rename compiler-allocated lanes onto stable mall protocol signals.
Every tile uses one 48x48 absolute snapping grid. Adjacent tiles intentionally place their external
dock constant-combinators on the exact same grid coordinate; Factorio blueprint-over-existing behavior
then retains both modules' wires on the shared marker.

The generator emits reusable HEAD / ASSEMBLER / RECYCLER tiles and a preassembled
[HEAD][P0][P1][Q0][Q1][R0] controller blueprint.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from factorio_circuit import Circuit, ModuleInterface, SignalId, compile_module
from factorio_circuit.compiler import CompilationResult
from factorio_circuit.ir.physical import InputPort, OutputPort, WireColor
from factorio_circuit.synthesis.interface import encode_blueprint_payload
from factorio_circuit.synthesis.placement import PlacementOptions

TILE_WIDTH = 48
TILE_HEIGHT = 48
_PREFERRED_LAYOUT_SHIFT = (4.0, 4.0)
_FIT_TOLERANCE = 1e-9

MALL_DISPATCH = SignalId("virtual", "signal-D")
MALL_LAUNCH = SignalId("virtual", "signal-L")
DEVICE_WORKING = SignalId("virtual", "signal-W")
DEVICE_FINISHED = SignalId("virtual", "signal-F")
DEVICE_INPUT_ENABLE = SignalId("virtual", "signal-I")
DEVICE_ACK_FINISHED = SignalId("virtual", "signal-A")

MODE_START = SignalId("virtual", "signal-C")
MODE_WAIT = SignalId("virtual", "signal-T")
SEEN = SignalId("virtual", "signal-S")

FACTORIO_BLUEPRINT_VERSION = 562949955518464

Direction = Literal["input", "output"]


@dataclass(frozen=True, slots=True)
class DockSpec:
    """One stable physical dock exposed by a compiled tile."""

    port: str
    direction: Direction
    external_position: tuple[float, float]
    fixed_signal: SignalId | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class CompiledTile:
    """One decorated reusable module blueprint."""

    label: str
    result: CompilationResult
    blueprint: dict[str, object]


def build_head_tile() -> Circuit:
    """Freeze the live roboport stock and launch the horizontal control/material buses."""

    circuit = Circuit("autonomous_mall_head_tile")
    stock = circuit.signals("stock")
    control = circuit.signals("control")
    dispatch = control.signal(MALL_DISPATCH) != 0

    snapshot = circuit.freeze("snapshot")
    old_snapshot = snapshot.sample()
    snapshot.set(stock, when=dispatch.logical_not())

    circuit.output("available_out", old_snapshot)
    circuit.output("control_out", control)
    circuit.output("frozen", dispatch)
    return circuit


def build_worker_tile(*, recipe_command: bool) -> Circuit:
    """Combine one reservation stage and one one-shot worker transaction FSM."""

    name = (
        "autonomous_mall_assembler_tile"
        if recipe_command
        else "autonomous_mall_recycler_tile"
    )
    circuit = Circuit(name)

    available = circuit.signals("available_in")
    control = circuit.signals("control_in")
    job_request = circuit.signals("job_request")
    job_recipe = circuit.signals("job_recipe") if recipe_command else None
    working = circuit.input("working") != 0
    finished = circuit.input("finished") != 0

    dispatch = control.signal(MALL_DISPATCH) != 0
    launch = control.signal(MALL_LAUNCH) != 0

    request_missing = (job_request - available).positive().any()
    candidate = dispatch * job_request.any() * request_missing.logical_not()
    if job_recipe is not None:
        candidate = candidate * job_recipe.any()
    accepted = candidate
    remaining = available - job_request.gate(accepted)

    start_token = circuit.constant_signals({MODE_START: 1})
    wait_token = circuit.constant_signals({MODE_WAIT: 1})
    seen_token = circuit.constant_signals({SEEN: 1})

    mode = circuit.freeze("mode")
    seen = circuit.freeze("seen")
    held_request = circuit.freeze("held_request")
    held_recipe = circuit.freeze("held_recipe") if recipe_command else None

    old_mode = mode.sample()
    old_seen = seen.sample()
    old_request = held_request.sample()
    old_recipe = held_recipe.sample() if held_recipe is not None else None

    starting = old_mode.signal(MODE_START) != 0
    waiting = old_mode.signal(MODE_WAIT) != 0
    idle = old_mode.any().logical_not()
    already_seen = old_seen.signal(SEEN) != 0

    start = (
        launch
        * accepted
        * idle
        * already_seen.logical_not()
        * working.logical_not()
        * finished.logical_not()
    )
    clear_seen = already_seen * accepted.logical_not()
    seen_change = start | clear_seen
    seen.set(seen_token.gate(start), when=seen_change)

    worker_started = starting * working
    worker_done = waiting * finished
    mode_change = start | worker_started | worker_done
    next_mode = start_token.gate(start) + wait_token.gate(worker_started)
    mode.set(next_mode, when=mode_change)

    held_request.set(job_request, when=start)
    if held_recipe is not None and job_recipe is not None:
        held_recipe.set(job_recipe, when=start)

    circuit.output("remaining_out", remaining)
    circuit.output("control_out", control)
    circuit.output("requester_demand", old_request.gate(starting))
    circuit.output("input_enable", starting)
    if old_recipe is not None:
        circuit.output("recipe", old_recipe)
    circuit.output("accepted", accepted)
    circuit.output("busy", starting | waiting)
    circuit.output("waiting_finished", waiting)
    circuit.output("ack_finished", worker_done)
    circuit.output("armed", already_seen.logical_not())
    return circuit


def build_assembler_tile() -> Circuit:
    return build_worker_tile(recipe_command=True)


def build_recycler_tile() -> Circuit:
    return build_worker_tile(recipe_command=False)


HEAD_INTERFACE = ModuleInterface(
    inputs={
        "stock": (8.0, 40.0),
        "control": (12.0, 2.0),
    },
    outputs={
        "available_out": (40.0, 6.0),
        "control_out": (40.0, 10.0),
        "frozen": (28.0, 2.0),
    },
    grid_size=(TILE_WIDTH, TILE_HEIGHT),
)

ASSEMBLER_INTERFACE = ModuleInterface(
    inputs={
        "available_in": (0.0, 6.0),
        "control_in": (0.0, 10.0),
        "job_request": (10.0, 2.0),
        "job_recipe": (16.0, 2.0),
        "working": (24.0, 40.0),
        "finished": (30.0, 40.0),
    },
    outputs={
        "remaining_out": (40.0, 6.0),
        "control_out": (40.0, 10.0),
        "requester_demand": (8.0, 40.0),
        "recipe": (14.0, 40.0),
        "input_enable": (18.0, 40.0),
        "ack_finished": (36.0, 40.0),
        "accepted": (24.0, 2.0),
        "busy": (30.0, 2.0),
        "waiting_finished": (34.0, 2.0),
        "armed": (38.0, 2.0),
    },
    grid_size=(TILE_WIDTH, TILE_HEIGHT),
)

RECYCLER_INTERFACE = ModuleInterface(
    inputs={
        "available_in": (0.0, 6.0),
        "control_in": (0.0, 10.0),
        "job_request": (10.0, 2.0),
        "working": (24.0, 40.0),
        "finished": (30.0, 40.0),
    },
    outputs={
        "remaining_out": (40.0, 6.0),
        "control_out": (40.0, 10.0),
        "requester_demand": (8.0, 40.0),
        "input_enable": (18.0, 40.0),
        "ack_finished": (36.0, 40.0),
        "accepted": (24.0, 2.0),
        "busy": (30.0, 2.0),
        "waiting_finished": (34.0, 2.0),
        "armed": (38.0, 2.0),
    },
    grid_size=(TILE_WIDTH, TILE_HEIGHT),
)

HEAD_DOCKS = (
    DockSpec("stock", "input", (12.0, 48.0), label="roboport stock"),
    DockSpec("available_out", "output", (48.0, 10.0), label="available bus"),
    DockSpec("control_out", "output", (48.0, 14.0), label="control bus"),
)

ASSEMBLER_DOCKS = (
    DockSpec("available_in", "input", (0.0, 10.0), label="available bus"),
    DockSpec("remaining_out", "output", (48.0, 10.0), label="available bus"),
    DockSpec("control_in", "input", (0.0, 14.0), label="control bus"),
    DockSpec("control_out", "output", (48.0, 14.0), label="control bus"),
    DockSpec("requester_demand", "output", (12.0, 48.0), label="requester demand"),
    DockSpec("recipe", "output", (18.0, 48.0), label="recipe"),
    DockSpec(
        "input_enable",
        "output",
        (22.0, 48.0),
        fixed_signal=DEVICE_INPUT_ENABLE,
        label="input enable",
    ),
    DockSpec(
        "working",
        "input",
        (28.0, 48.0),
        fixed_signal=DEVICE_WORKING,
        label="working",
    ),
    DockSpec(
        "finished",
        "input",
        (34.0, 48.0),
        fixed_signal=DEVICE_FINISHED,
        label="finished",
    ),
    DockSpec(
        "ack_finished",
        "output",
        (40.0, 48.0),
        fixed_signal=DEVICE_ACK_FINISHED,
        label="finish acknowledgement",
    ),
)

RECYCLER_DOCKS = tuple(dock for dock in ASSEMBLER_DOCKS if dock.port != "recipe")


def _placement() -> PlacementOptions:
    """Deterministic sparse-enough placement for reusable tile compilation."""

    return PlacementOptions(iterations=0, restarts=3, target_fill=0.62)


def _compile_base_tiles() -> tuple[tuple[str, CompilationResult, tuple[DockSpec, ...]], ...]:
    return (
        (
            "HEAD tile",
            compile_module(build_head_tile(), HEAD_INTERFACE, placement=_placement()),
            HEAD_DOCKS,
        ),
        (
            "ASSEMBLER worker tile",
            compile_module(build_assembler_tile(), ASSEMBLER_INTERFACE, placement=_placement()),
            ASSEMBLER_DOCKS,
        ),
        (
            "RECYCLER worker tile",
            compile_module(build_recycler_tile(), RECYCLER_INTERFACE, placement=_placement()),
            RECYCLER_DOCKS,
        ),
    )


def compile_manual_tiles() -> tuple[CompiledTile, ...]:
    """Compile and decorate the three reusable physical mall tiles."""

    return tuple(
        CompiledTile(label, result, _decorate_tile(label, result, docks))
        for label, result, docks in _compile_base_tiles()
    )


def _entity_positions(entities: list[object]) -> list[tuple[float, float]]:
    positions: list[tuple[float, float]] = []
    for entity in entities:
        assert isinstance(entity, dict)
        position = entity["position"]
        assert isinstance(position, dict)
        positions.append((float(position["x"]), float(position["y"])))
    return positions


def _fit_axis_shift(
    minimum: float,
    maximum: float,
    extent: float,
    preferred: float,
    *,
    axis: str,
) -> float:
    """Choose a translation that fits one routed axis inside the fixed tile envelope."""

    span = maximum - minimum
    if span > extent + _FIT_TOLERANCE:
        raise ValueError(
            f"compiled tile routed {axis}-span {span:.3f} exceeds {extent:.3f}-tile envelope"
        )

    lower = -minimum
    upper = extent - maximum
    if lower > upper + _FIT_TOLERANCE:  # Defensive duplicate of the span check above.
        raise ValueError(
            f"compiled tile has no legal {axis}-translation inside {extent:.3f}-tile envelope"
        )
    return min(max(preferred, lower), upper)


def _layout_shift(entities: list[object]) -> tuple[float, float]:
    """Fit actual routed entities, including relays, while preferring the historical +4 inset."""

    positions = _entity_positions(entities)
    if not positions:
        return _PREFERRED_LAYOUT_SHIFT
    xs = [position[0] for position in positions]
    ys = [position[1] for position in positions]
    return (
        _fit_axis_shift(
            min(xs),
            max(xs),
            float(TILE_WIDTH),
            _PREFERRED_LAYOUT_SHIFT[0],
            axis="x",
        ),
        _fit_axis_shift(
            min(ys),
            max(ys),
            float(TILE_HEIGHT),
            _PREFERRED_LAYOUT_SHIFT[1],
            axis="y",
        ),
    )


def _decorate_tile(
    label: str,
    result: CompilationResult,
    docks: tuple[DockSpec, ...],
) -> dict[str, object]:
    """Fit one compiled module into the tile and add stable external ABI adapters."""

    wrapper = deepcopy(result.blueprint_json)
    blueprint = wrapper["blueprint"]
    assert isinstance(blueprint, dict)
    blueprint["label"] = label
    blueprint["snap-to-grid"] = {"x": TILE_WIDTH, "y": TILE_HEIGHT}
    blueprint["absolute-snapping"] = True
    blueprint["position-relative-to-grid"] = {"x": 0, "y": 0}

    entities = blueprint.setdefault("entities", [])
    assert isinstance(entities, list)
    wires = blueprint.setdefault("wires", [])
    assert isinstance(wires, list)

    shift_x, shift_y = _layout_shift(entities)
    for entity in entities:
        assert isinstance(entity, dict)
        position = entity["position"]
        assert isinstance(position, dict)
        position["x"] = float(position["x"]) + shift_x
        position["y"] = float(position["y"]) + shift_y

    next_entity = max((int(entity["entity_number"]) for entity in entities), default=0) + 1
    for dock in docks:
        next_entity = _add_abi_dock(
            result,
            blueprint,
            dock,
            next_entity=next_entity,
        )

    _validate_tile_bounds(blueprint)
    blueprint["wires"] = [
        list(item)
        for item in sorted(
            {tuple(int(value) for value in wire) for wire in wires}
        )
    ]
    return wrapper


def _port(result: CompilationResult, dock: DockSpec) -> InputPort | OutputPort:
    ports = (
        result.physical_circuit.inputs
        if dock.direction == "input"
        else result.physical_circuit.outputs
    )
    matches = [port for port in ports if port.name == dock.port]
    if len(matches) != 1:
        raise ValueError(
            f"{dock.direction} dock {dock.port!r} resolved to {len(matches)} physical ports"
        )
    return matches[0]


def _port_color(result: CompilationResult, marker_entity: int) -> WireColor:
    colors = {
        wire.color
        for wire in result.layout.wires
        if wire.source_entity == marker_entity or wire.target_entity == marker_entity
    }
    if len(colors) != 1:
        raise ValueError(
            f"module port marker {marker_entity} must have exactly one internal wire color; "
            f"got {sorted(color.value for color in colors)}"
        )
    return next(iter(colors))


def _add_abi_dock(
    result: CompilationResult,
    blueprint: dict[str, object],
    dock: DockSpec,
    *,
    next_entity: int,
) -> int:
    """Add a red external marker plus one arithmetic isolation/renaming adapter."""

    port = _port(result, dock)
    internal_signal = port.signal
    if (internal_signal is None) != (dock.fixed_signal is None):
        if internal_signal is None:
            raise ValueError(f"vector dock {dock.port!r} cannot specify a fixed scalar signal")
        raise ValueError(f"scalar dock {dock.port!r} requires a stable fixed signal")

    color = _port_color(result, port.marker_entity)
    entities = blueprint["entities"]
    wires = blueprint["wires"]
    assert isinstance(entities, list)
    assert isinstance(wires, list)

    internal_position = _blueprint_entity_position(entities, port.marker_entity)
    external_position = dock.external_position
    adapter_position = (
        (internal_position[0] + external_position[0]) / 2.0,
        (internal_position[1] + external_position[1]) / 2.0,
    )

    adapter_id = next_entity
    dock_id = next_entity + 1
    description = dock.label or dock.port

    if dock.direction == "input":
        first_signal = dock.fixed_signal
        output_signal = internal_signal
        read_color = WireColor.RED
    else:
        first_signal = internal_signal
        output_signal = dock.fixed_signal
        read_color = color

    vector = internal_signal is None
    arithmetic_conditions: dict[str, object] = {
        "operation": "*",
        "second_constant": 1,
        "first_signal_networks": _network_selection(read_color),
    }
    if vector:
        arithmetic_conditions["first_signal"] = _signal_json(
            SignalId("virtual", "signal-each")
        )
        arithmetic_conditions["output_signal"] = _signal_json(
            SignalId("virtual", "signal-each")
        )
    else:
        assert first_signal is not None
        assert output_signal is not None
        arithmetic_conditions["first_signal"] = _signal_json(first_signal)
        arithmetic_conditions["output_signal"] = _signal_json(output_signal)

    entities.append(
        {
            "entity_number": adapter_id,
            "name": "arithmetic-combinator",
            "position": {"x": adapter_position[0], "y": adapter_position[1]},
            "direction": 4,
            "player_description": (
                f"MALL ABI {dock.direction.upper()} {description} — "
                f"external=red; internal={color.value}"
            ),
            "control_behavior": {"arithmetic_conditions": arithmetic_conditions},
        }
    )
    entities.append(
        {
            "entity_number": dock_id,
            "name": "constant-combinator",
            "position": {"x": external_position[0], "y": external_position[1]},
            "player_description": f"DOCK {description}",
        }
    )

    internal_connector = _constant_connector(color)
    if dock.direction == "input":
        _append_wire(wires, dock_id, 1, adapter_id, 1)
        _append_wire(
            wires,
            adapter_id,
            _arithmetic_output_connector(color),
            port.marker_entity,
            internal_connector,
        )
    else:
        _append_wire(
            wires,
            port.marker_entity,
            internal_connector,
            adapter_id,
            _arithmetic_input_connector(color),
        )
        _append_wire(wires, adapter_id, 3, dock_id, 1)
    return next_entity + 2


def _blueprint_entity_position(
    entities: list[object],
    entity_number: int,
) -> tuple[float, float]:
    for item in entities:
        assert isinstance(item, dict)
        if int(item["entity_number"]) != entity_number:
            continue
        position = item["position"]
        assert isinstance(position, dict)
        return float(position["x"]), float(position["y"])
    raise KeyError(entity_number)


def _signal_json(signal: SignalId) -> dict[str, str]:
    return {"type": signal.kind, "name": signal.name}


def _network_selection(color: WireColor) -> dict[str, bool]:
    return {
        "red": color is WireColor.RED,
        "green": color is WireColor.GREEN,
    }


def _constant_connector(color: WireColor) -> int:
    return 1 if color is WireColor.RED else 2


def _arithmetic_input_connector(color: WireColor) -> int:
    return 1 if color is WireColor.RED else 2


def _arithmetic_output_connector(color: WireColor) -> int:
    return 3 if color is WireColor.RED else 4


def _append_wire(
    wires: list[object],
    left_entity: int,
    left_connector: int,
    right_entity: int,
    right_connector: int,
) -> None:
    if left_entity > right_entity:
        left_entity, right_entity = right_entity, left_entity
        left_connector, right_connector = right_connector, left_connector
    wires.append([left_entity, left_connector, right_entity, right_connector])


def _validate_tile_bounds(blueprint: dict[str, object]) -> None:
    """Keep every generated entity center inside the fixed tile envelope."""

    entities = blueprint.get("entities", [])
    assert isinstance(entities, list)
    for item in entities:
        assert isinstance(item, dict)
        position = item["position"]
        assert isinstance(position, dict)
        x = float(position["x"])
        y = float(position["y"])
        if not 0.0 <= x <= TILE_WIDTH or not 0.0 <= y <= TILE_HEIGHT:
            raise ValueError(
                f"tile entity {item['entity_number']} escapes {TILE_WIDTH}x{TILE_HEIGHT} "
                f"envelope at {(x, y)}"
            )


def _compose_controller(
    head: CompiledTile,
    assembler: CompiledTile,
    recycler: CompiledTile,
) -> dict[str, object]:
    """Build one no-manual-horizontal-wiring six-tile controller blueprint."""

    sequence = (
        ("HEAD", head.blueprint),
        ("P0", assembler.blueprint),
        ("P1", assembler.blueprint),
        ("Q0", assembler.blueprint),
        ("Q1", assembler.blueprint),
        ("R0", recycler.blueprint),
    )

    entities: list[dict[str, object]] = []
    wires: set[tuple[int, int, int, int]] = set()
    shared_docks: dict[tuple[str, float, float], int] = {}
    next_entity = 1

    for tile_index, (instance_label, wrapper) in enumerate(sequence):
        blueprint = wrapper["blueprint"]
        assert isinstance(blueprint, dict)
        local_entities = blueprint.get("entities", [])
        local_wires = blueprint.get("wires", [])
        assert isinstance(local_entities, list)
        assert isinstance(local_wires, list)
        offset_x = tile_index * TILE_WIDTH
        id_map: dict[int, int] = {}

        for raw in local_entities:
            assert isinstance(raw, dict)
            item = deepcopy(raw)
            local_id = int(item["entity_number"])
            position = item["position"]
            assert isinstance(position, dict)
            position["x"] = float(position["x"]) + offset_x
            x = float(position["x"])
            y = float(position["y"])
            description = str(item.get("player_description", ""))
            is_dock = item.get("name") == "constant-combinator" and description.startswith("DOCK ")
            key = (str(item.get("name")), x, y)

            if is_dock and key in shared_docks:
                id_map[local_id] = shared_docks[key]
                continue

            global_id = next_entity
            next_entity += 1
            id_map[local_id] = global_id
            item["entity_number"] = global_id
            if description and not is_dock:
                item["player_description"] = f"{instance_label}: {description}"
            entities.append(item)
            if is_dock:
                shared_docks[key] = global_id

        for wire in local_wires:
            assert isinstance(wire, list)
            left, left_connector, right, right_connector = (int(value) for value in wire)
            global_left = id_map[left]
            global_right = id_map[right]
            if global_left > global_right:
                global_left, global_right = global_right, global_left
                left_connector, right_connector = right_connector, left_connector
            if global_left == global_right:
                continue
            wires.add((global_left, left_connector, global_right, right_connector))

    return {
        "blueprint": {
            "item": "blueprint",
            "label": "Autonomous mall tiled controller — HEAD P0 P1 Q0 Q1 R0",
            "version": FACTORIO_BLUEPRINT_VERSION,
            "entities": entities,
            "wires": [list(item) for item in sorted(wires)],
        }
    }


def build_blueprint_book() -> dict[str, object]:
    """Return the reusable tile set plus a fully preassembled controller."""

    head, assembler, recycler = compile_manual_tiles()
    assembled = _compose_controller(head, assembler, recycler)
    entries = [
        {"index": 0, **assembled},
        {"index": 1, **deepcopy(head.blueprint)},
        {"index": 2, **deepcopy(assembler.blueprint)},
        {"index": 3, **deepcopy(recycler.blueprint)},
    ]
    return {
        "blueprint_book": {
            "item": "blueprint-book",
            "label": "Autonomous mall snap-together tiles",
            "active_index": 0,
            "version": FACTORIO_BLUEPRINT_VERSION,
            "blueprints": entries,
        }
    }


def main() -> None:
    print(encode_blueprint_payload(build_blueprint_book()))


if __name__ == "__main__":
    main()
