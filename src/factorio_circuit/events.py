"""Public error categories shared by the semantic Event reference path."""


class EventScheduleError(ValueError):
    """Raised for malformed or incomplete Event schedules."""


class EventCausalityError(ValueError):
    """Raised for unsupported or malformed Event-triggered state structure."""


class EventThroughputError(ValueError):
    """Raised when a capture requirement exceeds its source guarantee."""


class EventCrossingError(ValueError):
    """Raised for unsupported or malformed semantic Level-to-Event crossings."""


class EventMaterializationError(ValueError):
    """Raised for invalid semantic Event trace materialization requests."""


class EventCompilationError(ValueError):
    """Raised when a semantic Event module reaches a Level/physical-only route."""
