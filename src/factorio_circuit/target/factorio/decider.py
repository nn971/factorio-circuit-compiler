"""Decider-combinator capability helpers."""

SUPPORTED_COMPARISONS = frozenset({"==", "!=", "<", "<=", ">", ">="})


FACTORIO_COMPARATOR: dict[str, str] = {
    "==": "=",
    "!=": "≠",
    "<": "<",
    "<=": "≤",
    ">": ">",
    ">=": "≥",
}
