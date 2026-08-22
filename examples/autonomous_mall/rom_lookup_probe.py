"""Generate a tiny in-game probe for the signal-keyed ROM lookup primitive.

The probe deliberately bypasses semantic/frontend lowering and constructs abstract
physical IR directly.  It validates the exact Factorio 2.0 operation needed by the mall
ROM without turning this example milestone into a general vector-lowering change.

Circuit:

    selected one-hot item --(one color)--\
                                      Each * Each -> Each -> Each + 0 -> signal-I
    ROM item:value page ----(other color)--/

The two input nets are declared conflicting, forcing physical synthesis to assign them
opposite wire colors.  Operand network selection then serializes the pairwise operation
as Each(red) * Each(green).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Mapping, Sequence

from factorio_circuit.blueprint.layout_encode import encode_layout_blueprint_string
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    Endpoint,
    NetConflict,
    Operand,
    OutputPort,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.synthesis import synthesize_layout

INFO = SignalId("virtual", "signal-I")


def build_rom_lookup_probe(
    *,
    selected_item: str,
    entries: Mapping[str, int],
) -> AbstractPhysicalCircuit:
    if selected_item not in entries:
        raise ValueError("selected item must exist in ROM entries")
    if not entries:
        raise ValueError("ROM entries must be non-empty")

    ordered = tuple(sorted((item, int(value)) for item, value in entries.items()))
    circuit = AbstractPhysicalCircuit(name="Autonomous mall ROM lookup probe")

    selected_id = 1
    rom_ids = tuple(range(2, 2 + ((len(ordered) + 19) // 20)))
    pairwise_id = 2 + len(rom_ids)
    reduce_id = pairwise_id + 1
    marker_id = reduce_id + 1

    circuit.entities.append(
        ConstantCombinator(
            id=selected_id,
            signals=((SignalId("item", selected_item), 1),),
            description="SELECTED TARGET — edit to another ROM item",
        )
    )
    for chunk_index, entity_id in enumerate(rom_ids):
        chunk = ordered[chunk_index * 20 : (chunk_index + 1) * 20]
        circuit.entities.append(
            ConstantCombinator(
                id=entity_id,
                signals=tuple((SignalId("item", item), value) for item, value in chunk),
                description=f"ROM PAGE chunk {chunk_index}",
            )
        )

    circuit.entities.extend(
        [
            ArithmeticCombinator(
                id=pairwise_id,
                operation="*",
                left=Operand(each=True, nets=(1,)),
                right=Operand(each=True, nets=(2,)),
                output_each=True,
                description="ROM lookup: Each(net A) * Each(net B) -> Each",
            ),
            ArithmeticCombinator(
                id=reduce_id,
                operation="+",
                left=Operand(each=True, nets=(3,)),
                right=Operand(constant=0),
                output_each=False,
                output_signal=INFO,
                description="Reduce selected packed word to signal-I",
            ),
            ConstantCombinator(
                id=marker_id,
                annotation_only=True,
                description="PROBE OUTPUT signal-I",
            ),
        ]
    )

    all_rom_signals = tuple(SignalId("item", item) for item, _ in ordered)
    circuit.nets.extend(
        [
            AbstractNet(
                id=1,
                signals=(),
                endpoints=(
                    Endpoint(selected_id, Connector.SINGLE),
                    Endpoint(pairwise_id, Connector.INPUT),
                ),
                label="selected target one-hot",
                fixed_signals=(SignalId("item", selected_item),),
                carries_dynamic_vector=True,
            ),
            AbstractNet(
                id=2,
                signals=(),
                endpoints=tuple(
                    [*(Endpoint(entity_id, Connector.SINGLE) for entity_id in rom_ids),
                     Endpoint(pairwise_id, Connector.INPUT)]
                ),
                label="ROM target-keyed page",
                fixed_signals=all_rom_signals,
                carries_dynamic_vector=True,
            ),
            AbstractNet(
                id=3,
                signals=(),
                endpoints=(
                    Endpoint(pairwise_id, Connector.OUTPUT),
                    Endpoint(reduce_id, Connector.INPUT),
                ),
                label="masked selected ROM word",
                fixed_signals=all_rom_signals,
                carries_dynamic_vector=True,
            ),
            AbstractNet(
                id=4,
                signals=(),
                endpoints=(
                    Endpoint(reduce_id, Connector.OUTPUT),
                    Endpoint(marker_id, Connector.SINGLE),
                ),
                label="scalar packed ROM word",
                fixed_signals=(INFO,),
            ),
        ]
    )
    circuit.net_conflicts.append(
        NetConflict(1, 2, "pairwise ROM lookup requires separate red/green input networks")
    )
    circuit.outputs.append(
        OutputPort(
            name="word",
            endpoint=Endpoint(marker_id, Connector.SINGLE),
            signal=INFO,
            phase=2,
        )
    )
    circuit.validate()
    return circuit


def encode_rom_lookup_probe(*, selected_item: str, entries: Mapping[str, int]) -> str:
    layout = synthesize_layout(build_rom_lookup_probe(selected_item=selected_item, entries=entries))
    return encode_layout_blueprint_string(layout)


def _parse_entry(value: str) -> tuple[str, int]:
    item, separator, count = value.partition("=")
    if not separator or not item:
        raise argparse.ArgumentTypeError("ROM entry must be ITEM=INTEGER")
    try:
        parsed = int(count)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ROM entry count must be an integer") from exc
    if not -(1 << 31) <= parsed < (1 << 31):
        raise argparse.ArgumentTypeError("ROM entry count must fit signed int32")
    return item, parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate signal-keyed ROM lookup probe blueprint")
    parser.add_argument("--selected", required=True, help="selected item signal")
    parser.add_argument("--entry", action="append", type=_parse_entry, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    entries = dict(args.entry)
    blueprint = encode_rom_lookup_probe(selected_item=args.selected, entries=entries)
    if args.output is None:
        print(blueprint)
    else:
        args.output.write_text(blueprint + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
