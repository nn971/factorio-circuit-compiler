from factorio_circuit import Circuit, SamplingPolicy
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator, Connector
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    DelayBusLane,
    DelayBusResource,
    DeliveryKind,
    ExactLifetime,
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
        item.operation: item for item in candidates if item.name.startswith("ordinary")
    }
    wire = next(
        item
        for item in candidates
        if item.operation == sum_op.id and item.name == "zero-delay wire sum"
    )

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
            PlannedDelivery(sum_op.operands[0], sum_op.id, 0, 2, DeliveryKind.REUSE),
            PlannedDelivery(sum_op.operands[1], sum_op.id, 1, 2, DeliveryKind.REUSE),
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
        wire_sums=(WireSumResource(sum_op.id, sum_op.operands[0], sum_op.operands[1], 2),),
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


def test_private_exact_lifetime_lowers_as_one_prefix_shared_chain() -> None:
    circuit = Circuit("mapped_private_transport")
    a = circuit.input("a")
    b = circuit.input("b")
    total = a + b
    circuit.output("early", total)
    circuit.output("late", total)
    module = lower_frontend(circuit)
    problem = build_stateless_level_mapping_problem(
        module,
        output_phases=(2, 3),
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )
    candidates = ordinary_candidates(problem)
    operation = problem.operations[0]
    candidate = candidates[0]
    assert len(problem.sources) == 2
    assert len(problem.sinks) == 2

    plan = RealizationPlan(
        realizations=(SelectedRealization(operation.id, candidate.id, 1, 1),),
        deliveries=(
            PlannedDelivery(
                operation.operands[0],
                operation.id,
                0,
                0,
                DeliveryKind.REUSE,
            ),
            PlannedDelivery(
                operation.operands[1],
                operation.id,
                1,
                0,
                DeliveryKind.REUSE,
            ),
            PlannedDelivery(
                operation.id,
                problem.sinks[0].id,
                None,
                2,
                DeliveryKind.PRIVATE_TRANSPORT,
                1,
            ),
            PlannedDelivery(
                operation.id,
                problem.sinks[1].id,
                None,
                3,
                DeliveryKind.PRIVATE_TRANSPORT,
                1,
            ),
        ),
        exact_lifetimes=(ExactLifetime(operation.id, 1, 3, (2, 3)),),
        wire_sums=(),
        entity_cost=1,
        transport_cost=2,
    )

    physical = lower_stateless_mapping_plan(module, problem, candidates, plan)

    arithmetic = [
        entity for entity in physical.entities if isinstance(entity, ArithmeticCombinator)
    ]
    assert len(arithmetic) == 3
    assert [port.phase for port in physical.outputs] == [2, 3]
    delay_entities = [
        entity
        for entity in arithmetic
        if entity.description is not None
        and entity.description.startswith("mapped exact transport")
    ]
    assert len(delay_entities) == 2


def test_delay_bus_plan_lowers_to_cost_exact_isolated_shared_trunk() -> None:
    circuit = Circuit("mapped_delay_bus")
    left = circuit.input("left")
    right = circuit.input("right")
    circuit.output("left-out", left)
    circuit.output("right-out", right)
    module = lower_frontend(circuit)
    problem = build_stateless_level_mapping_problem(
        module,
        output_phases=(6, 6),
        sampling_policy=SamplingPolicy.BEGINNING_OF_STEP,
    )
    assert not problem.operations
    left_source, right_source = problem.sources
    left_sink, right_sink = problem.sinks

    plan = RealizationPlan(
        realizations=(),
        deliveries=(
            PlannedDelivery(
                left_source.id,
                left_sink.id,
                None,
                6,
                DeliveryKind.BUS_TRANSPORT,
                0,
            ),
            PlannedDelivery(
                right_source.id,
                right_sink.id,
                None,
                6,
                DeliveryKind.BUS_TRANSPORT,
                0,
            ),
        ),
        exact_lifetimes=(
            ExactLifetime(left_source.id, 0, 6, (6,)),
            ExactLifetime(right_source.id, 0, 6, (6,)),
        ),
        wire_sums=(),
        entity_cost=0,
        transport_cost=8,
        delay_buses=(
            DelayBusResource(
                index=0,
                middle_start_phase=1,
                middle_end_phase=5,
                lanes=(
                    DelayBusLane(left_source.id, 0, 6, (6,)),
                    DelayBusLane(right_source.id, 0, 6, (6,)),
                ),
            ),
        ),
    )

    physical = lower_stateless_mapping_plan(module, problem, (), plan)

    arithmetic = [
        entity for entity in physical.entities if isinstance(entity, ArithmeticCombinator)
    ]
    ingress = [entity for entity in arithmetic if entity.description == "mapped delay bus ingress"]
    middle = [entity for entity in arithmetic if entity.description == "mapped shared delay bus 0"]
    egress = [
        entity
        for entity in arithmetic
        if entity.description is not None
        and entity.description.startswith("mapped delay bus 0 egress")
    ]
    assert len(arithmetic) == plan.transport_cost == 8
    assert len(ingress) == 2
    assert len(middle) == 4
    assert len(egress) == 2

    shared_nets = [
        net
        for net in physical.nets
        if net.label is not None and net.label.startswith("mapped shared delay bus 0 @")
    ]
    assert len(shared_nets) == 4
    assert all(len(net.signals) == 2 for net in shared_nets)
    assert len(physical.signal_conflicts) == 1
    assert [port.phase for port in physical.outputs] == [6, 6]
