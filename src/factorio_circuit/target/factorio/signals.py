"""Signal catalogue helpers for prototypes and tests."""

from factorio_circuit.ir.physical import SignalId

# Vanilla has many more usable signals; this deterministic pool is intentionally synthetic and large
# enough for compiler tests. Prototype loading will replace it later.
DEFAULT_VIRTUAL_SIGNAL_POOL: tuple[SignalId, ...] = tuple(
    [SignalId("virtual", f"signal-{chr(ord('A') + index)}") for index in range(26)]
    + [SignalId("virtual", f"signal-{index}") for index in range(10)]
    + [
        SignalId("virtual", f"signal-{name}")
        for name in (
            "red",
            "green",
            "blue",
            "yellow",
            "pink",
            "cyan",
            "white",
            "grey",
            "black",
            "check",
            "info",
            "dot",
            "star",
            "clock",
            "signal",
        )
    ]
)

SIGNAL_EACH = SignalId("virtual", "signal-each")
SIGNAL_EVERYTHING = SignalId("virtual", "signal-everything")
