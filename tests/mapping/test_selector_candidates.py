from factorio_circuit.ir.semantic import PayloadShape, VectorInput, VectorSelect
from factorio_circuit.mapping import MappingOperation, ordinary_candidate


def test_vector_select_uses_selector_candidate_family_before_vector_filter_base_class() -> None:
    semantic = VectorSelect(
        "select",
        VectorInput("values"),
        0,
        select_max=False,
        index=2,
    )
    operation = MappingOperation(
        2,
        "pick",
        PayloadShape.VECTOR,
        (1,),
        semantic,
    )

    candidate = ordinary_candidate(operation, candidate_id=7)

    assert candidate.name == "ordinary vector-select select"
    assert candidate.input_phase_offsets == (-1,)
    assert candidate.entity_cost == 1
