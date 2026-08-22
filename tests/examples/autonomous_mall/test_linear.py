from fractions import Fraction

import pytest

from examples.autonomous_mall.linear import InfeasibleLinearProgram, minimize_covering


def test_minimize_covering_single_constraint() -> None:
    solution = minimize_covering(
        costs=[Fraction(1), Fraction(2)],
        columns=[{0: Fraction(1)}, {0: Fraction(1)}],
        lower_bounds=[Fraction(3)],
    )
    assert solution.objective == 3
    assert solution.variables == (Fraction(3), Fraction(0))


def test_minimize_covering_handles_negative_stock_balance() -> None:
    solution = minimize_covering(
        costs=[Fraction(0), Fraction(1)],
        columns=[{0: Fraction(-1), 1: Fraction(1)}, {0: Fraction(1)}],
        lower_bounds=[Fraction(-1), Fraction(2)],
    )
    assert solution.objective == 1
    assert solution.variables == (Fraction(2), Fraction(1))


def test_minimize_covering_reports_infeasible_problem() -> None:
    with pytest.raises(InfeasibleLinearProgram):
        minimize_covering(costs=[], columns=[], lower_bounds=[Fraction(1)])
