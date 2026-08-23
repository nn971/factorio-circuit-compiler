import pytest

from factorio_circuit import Circuit, SamplingPolicy, SignalId
from factorio_circuit.ir.abstract_physical import ArithmeticCombinator, Connector, Endpoint
from factorio_circuit.ir.semantic import BinaryOp
from factorio_circuit.lowering.frontend_to_ir import lower_frontend
from factorio_circuit.mapping import (
    ImplementationKind,
    add_wire_sum_candidates,
    build_periodic_state_mapping_problem,
    lower_periodic_state_mapping_plan,
    ordinary_candidates,
    ordinary_state_candidates,
    solve_periodic_state_bus_mapping_problem,
)

_COUNT_SIGNAL = SignalId("virtual", "signal-C")


def _periodic_wire_sum_case():
    circuit = Circuit("periodic_wire_sum")
    x = circuit.input("x")
    y = circuit.input("y")
    enable = circuit.input("enable")

    left = x * 2
    right = y * 3
    total = left + right

    memory = circuit.freeze("memory")
    memory.set(
        circuit.constant_signals({_COUNT_SIGNAL: 1}) * total,
        when=enable != 0,
    )
    circuit.step(1)
    circuit.output("memory", memory.sample())

    module = lower_frontend(circuit)
    problem = build_periodic_state_mapping_problem(
        module,
        period=8,
        output_phases=(15,),
        sampling_policy=SamplingPolicy.ALAP,
    )
    return module, problem


def test_periodic_mapper_selects_and_lowers_binary_wire_sum() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    module, problem = _periodic_wire_sum_case()
    candidates = add_wire_sum_candidates(problem, ordinary_candidates(problem))
    state_candidates = ordinary_state_candidates(problem)

    solve = solve_periodic_state_bus_mapping_problem(
        problem,
        candidates=candidates,
        state_candidates=state_candidates,
        max_delay_buses=0,
        time_limit_seconds=5.0,
    )

    sum_operation = next(
        operation
        for operation in problem.operations
        if isinstance(operation.semantic, BinaryOp) and operation.semantic.op == "+"
    )
    realization = solve.plan.realization_for(sum_operation.id)
    selected = next(candidate for candidate in candidates if candidate.id == realization.candidate)

    assert solve.proven_optimal
    assert selected.kind is ImplementationKind.WIRE_SUM
    assert selected.entity_cost == 0
    assert len(solve.plan.wire_sums) == 1
    resource = solve.plan.wire_sums[0]
    assert resource.operation == sum_operation.id
    assert resource.phase == realization.output_phase
    assert all(
        solve.plan.realization_for(producer).output_phase == resource.phase
        for producer in (resource.left_producer, resource.right_producer)
    )

    root_deliveries = [
        delivery for delivery in solve.plan.deliveries if delivery.consumer == sum_operation.id
    ]
    assert len(root_deliveries) == 2
    assert all(delivery.kind.value == "reuse" for delivery in root_deliveries)
    assert all(delivery.phase == resource.phase for delivery in root_deliveries)

    lowered = lower_periodic_state_mapping_plan(
        module,
        problem,
        candidates,
        state_candidates,
        solve.plan,
    )

    assert lowered.cost_exact_after_known_surcharges
    assert len(lowered.circuit.signal_aliases) == 1
    alias = lowered.circuit.signal_aliases[0]
    contributor_entities = [
        entity
        for entity in lowered.circuit.entities
        if isinstance(entity, ArithmeticCombinator)
        and entity.operation == "*"
        and entity.output_signal in {alias.left, alias.right}
    ]
    assert len(contributor_entities) == 2

    shared_output_net = next(
        net
        for net in lowered.circuit.nets
        if all(
            Endpoint(entity.id, Connector.OUTPUT) in net.endpoints
            for entity in contributor_entities
        )
    )
    assert (
        len(
            [
                endpoint
                for endpoint in shared_output_net.endpoints
                if endpoint.connector is Connector.OUTPUT
                and endpoint.entity in {entity.id for entity in contributor_entities}
            ]
        )
        == 2
    )
