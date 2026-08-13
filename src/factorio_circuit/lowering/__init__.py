"""Semantic-to-target lowering entry points."""

from .ir_to_abstract_physical import lower_abstract_physical

__all__ = ["lower_abstract_physical"]
