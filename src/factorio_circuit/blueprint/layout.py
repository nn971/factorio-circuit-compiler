"""Compatibility placement exports.

Placement policy is owned by :mod:`factorio_circuit.synthesis.placement`; this module remains
only so older imports of ``blueprint.layout.row_positions`` continue to work.
"""

from factorio_circuit.synthesis.placement import row_positions

__all__ = ["row_positions"]
