"""Signal catalogue helpers for prototypes and tests."""

from factorio_circuit.ir.physical import SignalId

# Deterministic subset of real base-game virtual signals.  Selector pseudo-signals such as
# signal-each/everything/anything are intentionally excluded from allocation because they are
# circuit-language operands rather than ordinary independent signal lanes.
_VIRTUAL_SIGNAL_NAMES: tuple[str, ...] = tuple(
    [f"signal-{chr(ord('A') + index)}" for index in range(26)]
    + [f"signal-{index}" for index in range(10)]
    + [
        # Colors.
        "signal-red",
        "signal-green",
        "signal-blue",
        "signal-cyan",
        "signal-pink",
        "signal-yellow",
        "signal-white",
        "signal-grey",
        "signal-black",
        # General symbols.
        "signal-check",
        "signal-deny",
        "signal-no-entry",
        "signal-heart",
        "signal-alert",
        "signal-star",
        "signal-info",
        "signal-dot",
        # Letter/punctuation symbols.
        "signal-comma",
        "signal-letter-dot",
        "signal-exclamation-mark",
        "signal-question-mark",
        "signal-colon",
        "signal-slash",
        "signal-apostrophe",
        "signal-quotation-mark",
        "signal-ampersand",
        "signal-circumflex-accent",
        "signal-number-sign",
        "signal-percent",
        # Shapes.
        "shape-vertical",
        "shape-horizontal",
        "shape-diagonal",
        "shape-diagonal-2",
        "shape-curve",
        "shape-curve-2",
        "shape-curve-3",
        "shape-curve-4",
        "shape-cross",
        "shape-diagonal-cross",
        "shape-corner",
        "shape-corner-2",
        "shape-corner-3",
        "shape-corner-4",
        "shape-t",
        "shape-t-2",
        "shape-t-3",
        "shape-t-4",
        "shape-circle",
        # Eight compass arrows.
        "up-arrow",
        "up-right-arrow",
        "right-arrow",
        "down-right-arrow",
        "down-arrow",
        "down-left-arrow",
        "left-arrow",
        "up-left-arrow",
        # Miscellaneous arrows.
        "signal-rightwards-leftwards-arrow",
        "signal-upwards-downwards-arrow",
        "signal-shuffle",
        "signal-left-right-arrow",
        "signal-up-down-arrow",
        "signal-clockwise-circle-arrow",
        "signal-anticlockwise-circle-arrow",
        "signal-input",
        "signal-output",
        # Pictographs.
        "signal-fuel",
        "signal-lightning",
        "signal-battery-full",
        "signal-battery-mid-level",
        "signal-battery-low",
        "signal-radioactivity",
        "signal-thermometer-blue",
        "signal-thermometer-red",
        "signal-fire",
        "signal-explosion",
        "signal-snowflake",
        "signal-liquid",
        "signal-stack-size",
        "signal-recycle",
        "signal-trash-bin",
        "signal-science-pack",
        "signal-map-marker",
        "signal-white-flag",
        "signal-lock",
        "signal-unlock",
        "signal-mining",
        "signal-clock",
        "signal-hourglass",
        "signal-alarm",
        "signal-sun",
        "signal-moon",
        "signal-speed",
        "signal-skull",
        "signal-damage",
        "signal-weapon",
        "signal-ghost",
    ]
)

DEFAULT_VIRTUAL_SIGNAL_POOL: tuple[SignalId, ...] = tuple(
    SignalId("virtual", name) for name in _VIRTUAL_SIGNAL_NAMES
)

SIGNAL_EACH = SignalId("virtual", "signal-each")
SIGNAL_EVERYTHING = SignalId("virtual", "signal-everything")
