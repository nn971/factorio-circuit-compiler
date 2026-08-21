from factorio_circuit import Circuit, SamplingPolicy
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator, Connector
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    DeliveryKind,
    PlannedDelivery,
    RealizationPlan,
    SelectedRealization,
    WireSumResource,
    add_wire_sum_candidates,
    build_stateless_level_mapping_problem,
    lower_stateless_mapping_plan,
    ordinary_candidates,
)


def _mapped_wire_sum_fixture():
    circuit = Circuit("mapped_wire_sum")
    a = circuit.input("a")
    b = circuit.input("b")
    c = circuit.input("c")
    d = circuit.input("d")
    left = a * b
    right = c * d
    circuit.output("sum", left + right)
    module = lower_frontend(circuit)
    problem = build_stateless_level_mapping_problem(
        module,
        output_phases=(2,),
        sampling_policy=SamplingPolicy.ALAP,
    )
    candidates = add_wire_sum_candidates(problem, ordinary_candidates(problem))
    operations = {item.label: item for item in problem.operations}
    left_op = operations["binary *"]
    right_op = next(
        item for item in problem.operations if item.label == "binary *" and item.id != left_op.id
    )
    sum_op = operations["binary +"]

    ordinary_by_operation = {
        item.operation: item
        for item in candidates
        if item.name.startswith("ordinary")
    }
    wire = next(item for item in candidates if item.operation == sum_op.id and item.name == "zero-delay wire sum")

    deliveries = []
    for operation in (left_op, right_op):
        for operand_index, producer in enumerate(operation.operands):
            deliveries.append(
                PlannedDelivery(
                    producer=producer,
                    consumer=operation.id,
                    operand_index=operand_index,
                    phase=1,
                    kind=DeliveryKind.OBSERVE_AT,
                )
            )
    deliveries.extend(
        (
            PlannedDelivery(left_op.id, sum_op.id, 0, 2, DeliveryKind.REUSE),
            PlannedDelivery(right_op.id, sum_op.id, 1, 2, DeliveryKind.REUSE),
            PlannedDelivery(sum_op.id, problem.sinks[0].id, None, 2, DeliveryKind.REUSE),
        )
    )
    plan = RealizationPlan(
        realizations=(
            SelectedRealization(
                left_op.id,
                ordinary_by_operation[left_op.id].id,
                2,
                1,
            ),
            SelectedRealization(
                right_op.id,
                ordinary_by_operation[right_op.id].id,
                2,
                1,
            ),
            SelectedRealization(sum_op.id, wire.id, 2, 0),
        ),
        deliveries=tuple(deliveries),
        exact_lifetimes=(),
        wire_sums=(WireSumResource(sum_op.id, left_op.id, right_op.id, 2),),
        entity_cost=2,
        transport_cost=0,
    )
    return module, problem, candidates, plan, sum_op.id


def test_wire_sum_plan_lowers_to_shared_single_lane_net_without_add_combinator() -> None:
    module, problem, candidates, plan, sum_operation = _mapped_wire_sum_fixture()

    physical = lower_stateless_mapping_plan(module, problem, candidates, plan)

    arithmetic = [
        entity for entity in physical.entities if isinstance(entity, ArithmeticCombinator)
    ]
    assert len(arithmetic) == 2
    assert [entity.operation for entity in arithmetic] == ["*", "*"]

    shared = next(net for net in physical.nets if net.label == f"wire sum {sum_operation}")
    assert len(shared.signals) == 1
    output_endpoints = [
        endpoint for endpoint in shared.endpoints if endpoint.connector is Connector.OUTPUT
    ]
    assert len(output_endpoints) == 2
    assert len(shared.endpoints) == 3
    assert physical.outputs[0].phase == 2
    assert physical.outputs[0].signal == shared.signals[0]
