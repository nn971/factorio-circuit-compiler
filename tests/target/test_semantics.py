from factorio_circuit.target.factorio.semantics import apply_binary, i32


def test_i32_wraps() -> None:
    assert i32(2**31) == -(2**31)
    assert i32(-(2**31) - 1) == 2**31 - 1


def test_add_wraps() -> None:
    assert apply_binary("+", 2**31 - 1, 1) == -(2**31)


def test_division_truncates_toward_zero() -> None:
    assert apply_binary("/", -7, 3) == -2
    assert apply_binary("/", 7, -3) == -2
