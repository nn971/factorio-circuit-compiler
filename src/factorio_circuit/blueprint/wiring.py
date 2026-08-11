"""Factorio 2.x blueprint wire encoding helpers."""

from factorio_circuit.blueprint.routing import RoutingPlan


def blueprint_wires(plan: RoutingPlan) -> list[list[int]]:
    """Return deterministic top-level ``wires`` entries from a reach-safe routing plan."""

    items = {wire.as_factorio_tuple() for wire in plan.wires}
    return [list(item) for item in sorted(items)]
