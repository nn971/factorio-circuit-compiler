"""Factorio target signal catalogue helpers.

The compiler uses ordinary sendable base-game virtual signals as scratch-register identities during
physical allocation. Keep this pool deterministic and independent of Space Age so blueprints emitted
for the base game do not acquire expansion-only prototype dependencies.

Framebuffer/device protocols own a second large set of virtual-signal identities. Scratch signals
stay disjoint from those fixed ABI lanes; physical synthesis additionally removes any fixed semantic
signals from this pool before coloring.
"""

from factorio_circuit.ir.physical import SignalId


def _virtual(*names: str) -> tuple[SignalId, ...]:
    return tuple(SignalId("virtual", name) for name in names)


# Exact base-game virtual-signal prototype names.  Letters/numbers/basic icons are the historical
# 51-lane compiler palette.  Factorio 2.x mathematical symbols add another 14 scratch identities
# without consuming the virtual-signal namespace reserved by the 16x16 framebuffer ABI.
#
# The combinator meta-signals signal-each/signal-anything/signal-everything and parameter
# placeholders
# are deliberately excluded because they are selectors/operators rather than ordinary numeric lanes.
_BASE_GAME_SCRATCH_NAMES: tuple[str, ...] = (
    *(f"signal-{chr(ord('A') + index)}" for index in range(26)),
    *(f"signal-{index}" for index in range(10)),
    "signal-red",
    "signal-green",
    "signal-blue",
    "signal-yellow",
    "signal-pink",
    "signal-cyan",
    "signal-white",
    "signal-grey",
    "signal-black",
    "signal-check",
    "signal-info",
    "signal-dot",
    "signal-star",
    "signal-clock",
    "signal-deny",
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
)

if len(_BASE_GAME_SCRATCH_NAMES) != len(set(_BASE_GAME_SCRATCH_NAMES)):  # pragma: no cover
    raise AssertionError("base-game scratch signal catalogue contains duplicates")

DEFAULT_VIRTUAL_SIGNAL_POOL: tuple[SignalId, ...] = _virtual(*_BASE_GAME_SCRATCH_NAMES)

SIGNAL_EACH = SignalId("virtual", "signal-each")
SIGNAL_ANYTHING = SignalId("virtual", "signal-anything")
SIGNAL_EVERYTHING = SignalId("virtual", "signal-everything")
