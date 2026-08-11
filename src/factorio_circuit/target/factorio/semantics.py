"""Reference semantics for Factorio-style signed 32-bit arithmetic and comparisons."""

from __future__ import annotations

I32_MASK = 0xFFFFFFFF
I32_SIGN = 0x80000000


def i32(value: int) -> int:
    """Wrap *value* to a signed 32-bit integer."""

    value &= I32_MASK
    return value - (1 << 32) if value & I32_SIGN else value


def trunc_div(left: int, right: int) -> int:
    """Integer division truncated toward zero."""

    if right == 0:
        return 0
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


def apply_binary(operation: str, left: int, right: int) -> int:
    """Apply an arithmetic operation using Factorio-style i32 wrapping."""

    left = i32(left)
    right = i32(right)

    if operation == "+":
        result = left + right
    elif operation == "-":
        result = left - right
    elif operation == "*":
        result = left * right
    elif operation in {"/", "//"}:
        result = trunc_div(left, right)
    elif operation == "%":
        result = 0 if right == 0 else left - trunc_div(left, right) * right
    elif operation == "&":
        result = left & right
    elif operation == "|":
        result = left | right
    elif operation == "^":
        result = left ^ right
    elif operation == "<<":
        result = left << (right & 31)
    elif operation == ">>":
        result = left >> (right & 31)
    elif operation == "**":
        result = 0 if right < 0 else pow(left, right, 1 << 32)
    else:
        raise ValueError(f"unsupported operation {operation!r}")

    return i32(result)


def apply_compare(operation: str, left: int, right: int) -> bool:
    """Evaluate a supported Factorio-style comparison."""

    left = i32(left)
    right = i32(right)
    if operation == "==":
        return left == right
    if operation == "!=":
        return left != right
    if operation == "<":
        return left < right
    if operation == "<=":
        return left <= right
    if operation == ">":
        return left > right
    if operation == ">=":
        return left >= right
    raise ValueError(f"unsupported comparison {operation!r}")
