"""Generate an in-game probe for the second-stage ROM record mux.

The already-validated target lookup is applied independently to several record pages.
Each page is reduced onto a distinct internal *slot signal*, producing a vector:

    slot_0 = descriptor_0
    slot_1 = descriptor_1
    ...

A numeric scan pointer is decoded against a constant slot-address vector into a one-hot
slot signal.  Pairwise ``Each(pointer) * Each(record-vector)`` then selects exactly one
record, which is reduced to ``signal-I``.

This tests the key primitives needed by the eventual clocked scanner without requiring
quality-aware assembler recipe signals yet.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from factorio_circuit.blueprint.layout_encode import encode_layout_blueprint_string
from factorio_circuit.ir.abstract_physical import (
    AbstractNet,
    AbstractPhysicalCircuit,
    ArithmeticCombinator,
    Connector,
    ConstantCombinator,
    DeciderCombinator,
    Endpoint,
    NetConflict,
    Operand,
    OutputPort,
)
from factorio_circuit.ir.physical import SignalId
from factorio_circuit.synthesis import synthesize_layout

POINTER = SignalId("virtual", "signal-P")
INFO = SignalId("virtual", "signal-I")
EACH = SignalId("virtual", "signal-each")
_SLOT_NAMES = tuple(f"signal-{letter}" for letter in "ABCDEFGHJKLMNOPQRSTUV")


def build_rom_record_mux_probe(
    *,
    selected_item: str,
    records: Sequence[int],
    pointer_index: int,
) -> AbstractPhysicalCircuit:
    words = tuple(int(value) for value in records)
    if not words:
        raise ValueError("at least one record word is required")
    if len(words) > len(_SLOT_NAMES):
        raise ValueError(f"probe supports at most {len(_SLOT_NAMES)} record slots")
    if not 0 <= pointer_index < len(words):
        raise ValueError("pointer_index is outside record range")
    if any(not -(1 << 31) <= value < (1 << 31) for value in words):
        raise ValueError("record words must fit signed int32")

    circuit = AbstractPhysicalCircuit(name="Autonomous mall ROM record mux probe")
    next_entity = 1
    next_net = 1

    selected_id = next_entity
    next_entity += 1
    pointer_id = next_entity
    next_entity += 1
    slot_address_id = next_entity
    next_entity += 1

    slot_signals = tuple(SignalId("virtual", name) for name in _SLOT_NAMES[: len(words)])
    circuit.entities.extend(
        [
            ConstantCombinator(
                id=selected_id,
                signals=((SignalId("item", selected_item), 1),),
                description="SELECTED TARGET — target-key input",
            ),
            ConstantCombinator(
                id=pointer_id,
                signals=((POINTER, pointer_index + 1),),
                description="SCAN POINTER — set to slot index + 1",
            ),
            ConstantCombinator(
                id=slot_address_id,
                signals=tuple((signal, index + 1) for index, signal in enumerate(slot_signals)),
                description="RECORD SLOT ADDRESS VECTOR",
            ),
        ]
    )

    selected_net = next_net
    next_net += 1
    page_output_nets: list[int] = []
    page_ids: list[int] = []
    pairwise_ids: list[int] = []
    reducer_ids: list[int] = []

    for index, (word, slot_signal) in enumerate(zip(words, slot_signals, strict=True)):
        page_id = next_entity
        next_entity += 1
        pairwise_id = next_entity
        next_entity += 1
        reducer_id = next_entity
        next_entity += 1
        page_net = next_net
        next_net += 1
        masked_net = next_net
        next_net += 1

        page_ids.append(page_id)
        pairwise_ids.append(pairwise_id)
        reducer_ids.append(reducer_id)
        page_output_nets.append(masked_net)
        circuit.entities.extend(
            [
                ConstantCombinator(
                    id=page_id,
                    signals=((SignalId("item", selected_item), word),),
                    description=f"TARGET-KEYED RECORD PAGE {index}",
                ),
                ArithmeticCombinator(
                    id=pairwise_id,
                    operation="*",
                    left=Operand(each=True, nets=(selected_net,)),
                    right=Operand(each=True, nets=(page_net,)),
                    output_each=True,
                    description=f"TARGET LOOKUP record {index}",
                ),
                DeciderCombinator(
                    id=reducer_id,
                    comparator="!=",
                    left=Operand(each=True, nets=(masked_net,)),
                    right=Operand(constant=0),
                    output_signal=slot_signal,
                    output_copy_count_from_input=True,
                    copy_count_nets=(masked_net,),
                    description=f"RECORD {index} -> {slot_signal.name}",
                ),
            ]
        )
        circuit.nets.extend(
            [
                AbstractNet(
                    id=page_net,
                    signals=(),
                    endpoints=(
                        Endpoint(page_id, Connector.SINGLE),
                        Endpoint(pairwise_id, Connector.INPUT),
                    ),
                    fixed_signals=(SignalId("item", selected_item),),
                    carries_dynamic_vector=True,
                    label=f"record page {index}",
                ),
                AbstractNet(
                    id=masked_net,
                    signals=(),
                    endpoints=(
                        Endpoint(pairwise_id, Connector.OUTPUT),
                        Endpoint(reducer_id, Connector.INPUT),
                    ),
                    fixed_signals=(SignalId("item", selected_item),),
                    carries_dynamic_vector=True,
                    label=f"target-masked record {index}",
                ),
            ]
        )
        circuit.net_conflicts.append(
            NetConflict(
                selected_net,
                page_net,
                f"record {index} target lookup requires opposite input colors",
            )
        )

    circuit.nets.append(
        AbstractNet(
            id=selected_net,
            signals=(),
            endpoints=tuple(
                [Endpoint(selected_id, Connector.SINGLE)]
                + [Endpoint(entity_id, Connector.INPUT) for entity_id in pairwise_ids]
            ),
            fixed_signals=(SignalId("item", selected_item),),
            carries_dynamic_vector=True,
            label="selected target one-hot",
        )
    )

    record_vector_net = next_net
    next_net += 1
    pointer_scalar_net = next_net
    next_net += 1
    slot_address_net = next_net
    next_net += 1
    pointer_onehot_net = next_net
    next_net += 1
    selected_record_net = next_net
    next_net += 1
    output_net = next_net
    next_net += 1

    pointer_decode_id = next_entity
    next_entity += 1
    record_mux_id = next_entity
    next_entity += 1
    final_reduce_id = next_entity
    next_entity += 1
    marker_id = next_entity

    circuit.entities.extend(
        [
            DeciderCombinator(
                id=pointer_decode_id,
                comparator="==",
                left=Operand(each=True, nets=(slot_address_net,)),
                right=Operand(signal=POINTER, nets=(pointer_scalar_net,)),
                output_signal=EACH,
                output_constant=1,
                description="POINTER decode: Each(slot address) == P -> Each=1",
            ),
            ArithmeticCombinator(
                id=record_mux_id,
                operation="*",
                left=Operand(each=True, nets=(pointer_onehot_net,)),
                right=Operand(each=True, nets=(record_vector_net,)),
                output_each=True,
                description="RECORD MUX: Each(pointer) * Each(records) -> Each",
            ),
            DeciderCombinator(
                id=final_reduce_id,
                comparator="!=",
                left=Operand(each=True, nets=(selected_record_net,)),
                right=Operand(constant=0),
                output_signal=INFO,
                output_copy_count_from_input=True,
                copy_count_nets=(selected_record_net,),
                description="SELECTED RECORD -> signal-I",
            ),
            ConstantCombinator(
                id=marker_id,
                annotation_only=True,
                description="PROBE OUTPUT signal-I",
            ),
        ]
    )

    circuit.nets.extend(
        [
            AbstractNet(
                id=record_vector_net,
                signals=(),
                endpoints=tuple(
                    [Endpoint(entity_id, Connector.OUTPUT) for entity_id in reducer_ids]
                    + [Endpoint(record_mux_id, Connector.INPUT)]
                ),
                fixed_signals=slot_signals,
                carries_dynamic_vector=True,
                label="all target-selected record words by slot signal",
            ),
            AbstractNet(
                id=pointer_scalar_net,
                signals=(),
                endpoints=(
                    Endpoint(pointer_id, Connector.SINGLE),
                    Endpoint(pointer_decode_id, Connector.INPUT),
                ),
                fixed_signals=(POINTER,),
                label="numeric scan pointer",
            ),
            AbstractNet(
                id=slot_address_net,
                signals=(),
                endpoints=(
                    Endpoint(slot_address_id, Connector.SINGLE),
                    Endpoint(pointer_decode_id, Connector.INPUT),
                ),
                fixed_signals=slot_signals,
                carries_dynamic_vector=True,
                label="slot signal -> index+1 dictionary",
            ),
            AbstractNet(
                id=pointer_onehot_net,
                signals=(),
                endpoints=(
                    Endpoint(pointer_decode_id, Connector.OUTPUT),
                    Endpoint(record_mux_id, Connector.INPUT),
                ),
                fixed_signals=slot_signals,
                carries_dynamic_vector=True,
                label="one-hot record slot pointer",
            ),
            AbstractNet(
                id=selected_record_net,
                signals=(),
                endpoints=(
                    Endpoint(record_mux_id, Connector.OUTPUT),
                    Endpoint(final_reduce_id, Connector.INPUT),
                ),
                fixed_signals=slot_signals,
                carries_dynamic_vector=True,
                label="selected record slot word",
            ),
            AbstractNet(
                id=output_net,
                signals=(),
                endpoints=(
                    Endpoint(final_reduce_id, Connector.OUTPUT),
                    Endpoint(marker_id, Connector.SINGLE),
                ),
                fixed_signals=(INFO,),
                label="selected descriptor scalar",
            ),
        ]
    )
    circuit.net_conflicts.extend(
        [
            NetConflict(
                slot_address_net,
                pointer_scalar_net,
                "pointer decoder compares slot addresses against scalar P",
            ),
            NetConflict(
                pointer_onehot_net,
                record_vector_net,
                "record mux requires opposite pointer/data input colors",
            ),
        ]
    )
    circuit.outputs.append(
        OutputPort(
            name="record",
            endpoint=Endpoint(marker_id, Connector.SINGLE),
            signal=INFO,
            phase=4,
        )
    )
    circuit.validate()
    return circuit


def encode_rom_record_mux_probe(
    *,
    selected_item: str,
    records: Sequence[int],
    pointer_index: int,
) -> str:
    layout = synthesize_layout(
        build_rom_record_mux_probe(
            selected_item=selected_item,
            records=records,
            pointer_index=pointer_index,
        )
    )
    return encode_layout_blueprint_string(layout)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate autonomous-mall ROM record mux probe")
    parser.add_argument("--selected", required=True, help="selected target item signal")
    parser.add_argument(
        "--record",
        action="append",
        type=int,
        required=True,
        help="signed int32 record word; repeat for multiple slots",
    )
    parser.add_argument(
        "--pointer",
        type=int,
        default=0,
        help="zero-based selected record slot",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    blueprint = encode_rom_record_mux_probe(
        selected_item=args.selected,
        records=args.record,
        pointer_index=args.pointer,
    )
    if args.output is None:
        print(blueprint)
    else:
        args.output.write_text(blueprint + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
