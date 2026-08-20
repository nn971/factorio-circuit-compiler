"""Factorio target signal catalogue helpers.

The compiler uses ordinary sendable base-game virtual signals as scratch-register identities during
physical allocation. Keep this pool deterministic and independent of Space Age so blueprints emitted
for the base game do not acquire expansion-only prototype dependencies.

The three combinator meta-signals (Each, Anything, Everything) are deliberately excluded: they are
operators/selectors, not ordinary numeric lanes. Parameter placeholder signals are excluded for the
same reason. Fixed semantic signals are removed from the available pool by physical synthesis before
scratch allocation.
"""

from factorio_circuit.ir.physical import SignalId


def _virtual(*names: str) -> tuple[SignalId, ...]:
    return tuple(SignalId("virtual", name) for name in names)


# Exact prototype names from Factorio's base/prototypes/signal.lua.  This intentionally contains
# only ordinary base-game virtual signals that can act as numeric circuit lanes.  Do not add the
# combinator meta-signals signal-each/signal-anything/signal-everything or parameter placeholders.
_BASE_GAME_SCRATCH_NAMES: tuple[str, ...] = (
    *(f"signal-{chr(ord('A') + index)}" for index in range(26)),
    *(f"signal-{index}" for index in range(10)),
    # Colors and generic icons.
    "signal-red",
    "signal-green",
    "signal-blue",
    "signal-cyan",
    "signal-pink",
    "signal-yellow",
    "signal-white",
    "signal-grey",
    "signal-black",
    "signal-check",
    "signal-deny",
    "signal-no-entry",
    "signal-heart",
    "signal-alert",
    "signal-star",
    "signal-info",
    "signal-dot",
    "signal-clock",
    # Punctuation.
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
    # Mathematical symbols.
    "signal-plus",
    "signal-minus",
    "signal-multiplication",
    "signal-division",
    "signal-equal",
    "signal-not-equal",
    "signal-less-than",
    "signal-greater-than",
    "signal-less-than-or-equal-to",
    "signal-greater-than-or-equal-to",
    "signal-left-parenthesis",
    "signal-right-parenthesis",
    "signal-left-square-bracket",
    "signal-right-square-bracket",
    # Shapes. These prototype names intentionally do not use the signal- prefix.
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
    # Direction/flow symbols.
    "up-arrow",
    "up-right-arrow",
    "right-arrow",
    "down-right-arrow",
    "down-arrow",
    "down-left-arrow",
    "left-arrow",
    "up-left-arrow",
    "signal-rightwards-leftwards-arrow",
    "signal-upwards-downwards-arrow",
    "signal-shuffle",
    "signal-left-right-arrow",
    "signal-up-down-arrow",
    "signal-clockwise-circle-arrow",
    "signal-anticlockwise-circle-arrow",
    "signal-input",
    "signal-output",
    # Base pictographs useful as additional scratch identities.
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
)

if len(_BASE_GAME_SCRATCH_NAMES) != len(set(_BASE_GAME_SCRATCH_NAMES)):  # pragma: no cover
    raise AssertionError("base-game scratch signal catalogue contains duplicates")

DEFAULT_VIRTUAL_SIGNAL_POOL: tuple[SignalId, ...] = _virtual(*_BASE_GAME_SCRATCH_NAMES)

SIGNAL_EACH = SignalId("virtual", "signal-each")
SIGNAL_ANYTHING = SignalId("virtual", "signal-anything")
SIGNAL_EVERYTHING = SignalId("virtual", "signal-everything")
