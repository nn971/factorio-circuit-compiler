"""Legacy import module for the former AST-only builtins."""

from factorio_circuit.frontend.symbolic import AccumulatorReg, FreezeReg, SignalsExpr

Signals = SignalsExpr


def tick(n: int = 1) -> None:
    raise RuntimeError("global tick() was removed; call Circuit.tick() on the elaboration context")


def tick_until(n: int) -> None:
    raise RuntimeError(
        "global tick_until() was removed; call Circuit.tick_until() on the elaboration context"
    )


__all__ = ["AccumulatorReg", "FreezeReg", "Signals", "tick", "tick_until"]
