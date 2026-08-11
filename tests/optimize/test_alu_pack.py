from factorio_circuit import Circuit
from factorio_circuit.optimize.alu_pack import find_packable_arithmetic


def _three_multiplies() -> Circuit:
    c = Circuit("three_multiplies")
    a = c.input("a")
    b = c.input("b")
    d = c.input("c")
    c.output("x", a * 2)
    c.output("y", b * 2)
    c.output("z", d * 2)
    return c


def _three_adds() -> Circuit:
    c = Circuit("three_adds")
    a = c.input("a")
    b = c.input("b")
    d = c.input("c")
    c.output("x", a + 1)
    c.output("y", b + 1)
    c.output("z", d + 1)
    return c


def test_three_multiplies_form_one_pack_group() -> None:
    groups = find_packable_arithmetic(_three_multiplies().build())
    matching = [group for group in groups if group.operation == "*" and group.constant == 2]
    assert len(matching) == 1
    assert len(matching[0].operations) == 3


def test_non_zero_preserving_addition_is_not_pack_compatible() -> None:
    groups = find_packable_arithmetic(_three_adds().build())
    assert all(len(group.operations) == 1 for group in groups)
