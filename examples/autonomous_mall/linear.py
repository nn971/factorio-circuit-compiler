"""Small exact two-phase simplex used by the autonomous mall reference model.

The example deliberately keeps this solver local rather than adding an optimization
dependency or exposing linear programming as compiler API. It is sized for reference
planning and tests, not for physical combinator lowering.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping, Sequence


class LinearProgramError(RuntimeError):
    """Base error for the example-local exact LP solver."""


class InfeasibleLinearProgram(LinearProgramError):
    """The linear program has no feasible non-negative solution."""


class UnboundedLinearProgram(LinearProgramError):
    """The objective can decrease without bound."""


@dataclass(frozen=True)
class LinearSolution:
    objective: Fraction
    variables: tuple[Fraction, ...]


@dataclass
class _Row:
    basic: int
    rhs: Fraction
    coeff: dict[int, Fraction]


def minimize_covering(
    *,
    costs: Sequence[Fraction],
    columns: Sequence[Mapping[int, Fraction]],
    lower_bounds: Sequence[Fraction],
) -> LinearSolution:
    """Minimize ``costs*x`` subject to ``A*x >= lower_bounds`` and ``x >= 0``.

    ``columns[j][i]`` is ``A[i, j]``. Arithmetic is exact ``Fraction`` arithmetic.
    """

    variable_count = len(costs)
    if len(columns) != variable_count:
        raise ValueError("cost and column counts differ")
    row_count = len(lower_bounds)
    if any(row < 0 or row >= row_count for column in columns for row in column):
        raise ValueError("column row index out of range")

    rows: list[_Row] = []
    nonbasic: set[int] = set(range(variable_count))
    artificial: set[int] = set()
    next_var = variable_count

    for row_index, bound_value in enumerate(lower_bounds):
        bound = Fraction(bound_value)
        dense = {j: Fraction(columns[j].get(row_index, 0)) for j in range(variable_count)}
        dense = {j: value for j, value in dense.items() if value}
        if bound >= 0:
            surplus = next_var
            next_var += 1
            artificial_var = next_var
            next_var += 1
            coeff = {j: -value for j, value in dense.items()}
            coeff[surplus] = Fraction(1)
            rows.append(_Row(artificial_var, bound, coeff))
            nonbasic.add(surplus)
            artificial.add(artificial_var)
        else:
            slack = next_var
            next_var += 1
            rows.append(_Row(slack, -bound, dict(dense)))

    total_vars = next_var
    phase1_costs = [Fraction(0) for _ in range(total_vars)]
    for variable in artificial:
        phase1_costs[variable] = Fraction(1)

    _simplex(rows, nonbasic, phase1_costs)
    phase1_objective, _ = _objective(rows, nonbasic, phase1_costs)
    if phase1_objective != 0:
        raise InfeasibleLinearProgram("material balance is infeasible")

    _remove_artificial(rows, nonbasic, artificial)

    phase2_costs = [Fraction(0) for _ in range(total_vars)]
    for index, cost in enumerate(costs):
        phase2_costs[index] = Fraction(cost)
    _simplex(rows, nonbasic, phase2_costs)
    objective, _ = _objective(rows, nonbasic, phase2_costs)

    values = [Fraction(0) for _ in range(variable_count)]
    for row in rows:
        if row.basic < variable_count:
            values[row.basic] = row.rhs
    return LinearSolution(objective=objective, variables=tuple(values))


def _simplex(rows: list[_Row], nonbasic: set[int], costs: Sequence[Fraction]) -> None:
    while True:
        _, reduced = _objective(rows, nonbasic, costs)
        entering = next((j for j in sorted(nonbasic) if reduced.get(j, Fraction(0)) < 0), None)
        if entering is None:
            return

        eligible = [
            (row.rhs / (-row.coeff[entering]), index)
            for index, row in enumerate(rows)
            if row.coeff.get(entering, Fraction(0)) < 0
        ]
        if not eligible:
            raise UnboundedLinearProgram("objective is unbounded")
        _, leaving_index = min(eligible, key=lambda pair: (pair[0], rows[pair[1]].basic))
        _pivot(rows, nonbasic, leaving_index, entering)


def _objective(
    rows: Sequence[_Row], nonbasic: set[int], costs: Sequence[Fraction]
) -> tuple[Fraction, dict[int, Fraction]]:
    constant = Fraction(0)
    reduced = {j: Fraction(costs[j]) for j in nonbasic}
    for row in rows:
        basic_cost = Fraction(costs[row.basic])
        if not basic_cost:
            continue
        constant += basic_cost * row.rhs
        for variable, coefficient in row.coeff.items():
            if variable in nonbasic:
                reduced[variable] = reduced.get(variable, Fraction(0)) + basic_cost * coefficient
    return constant, reduced


def _pivot(rows: list[_Row], nonbasic: set[int], row_index: int, entering: int) -> None:
    pivot_row = rows[row_index]
    leaving = pivot_row.basic
    pivot = pivot_row.coeff.get(entering, Fraction(0))
    if not pivot:
        raise LinearProgramError("zero pivot")

    old_coeff = dict(pivot_row.coeff)
    old_coeff.pop(entering, None)
    new_rhs = -pivot_row.rhs / pivot
    new_coeff = {leaving: Fraction(1, 1) / pivot}
    for variable, coefficient in old_coeff.items():
        value = -coefficient / pivot
        if value:
            new_coeff[variable] = value
    pivot_row.basic = entering
    pivot_row.rhs = new_rhs
    pivot_row.coeff = new_coeff

    for index, row in enumerate(rows):
        if index == row_index:
            continue
        factor = row.coeff.pop(entering, Fraction(0))
        if not factor:
            continue
        row.rhs += factor * new_rhs
        for variable, coefficient in new_coeff.items():
            value = row.coeff.get(variable, Fraction(0)) + factor * coefficient
            if value:
                row.coeff[variable] = value
            else:
                row.coeff.pop(variable, None)
        if row.rhs < 0:
            raise LinearProgramError("pivot lost primal feasibility")

    nonbasic.remove(entering)
    nonbasic.add(leaving)


def _remove_artificial(rows: list[_Row], nonbasic: set[int], artificial: set[int]) -> None:
    index = 0
    while index < len(rows):
        row = rows[index]
        if row.basic not in artificial:
            index += 1
            continue
        if row.rhs != 0:
            raise InfeasibleLinearProgram("positive artificial basic variable after phase 1")
        entering = next(
            (
                variable
                for variable in sorted(nonbasic)
                if variable not in artificial and row.coeff.get(variable, Fraction(0))
            ),
            None,
        )
        if entering is None:
            rows.pop(index)
            continue
        _pivot(rows, nonbasic, index, entering)
        index += 1

    nonbasic.difference_update(artificial)
    for row in rows:
        for variable in artificial:
            row.coeff.pop(variable, None)
