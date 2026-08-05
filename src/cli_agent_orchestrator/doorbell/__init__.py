"""Doorbell ingress: short external triggers to the king inbox (ADT-6 / Item 5)."""

from cli_agent_orchestrator.doorbell.delivery import deliver_doorbell_trigger
from cli_agent_orchestrator.doorbell.routing import resolve_king_terminal_id
from cli_agent_orchestrator.doorbell.validation import (
    DOORBELL_MAX_TRIGGER_CHARS,
    DoorbellValidationError,
    is_doorbell_trigger,
    validate_doorbell_trigger,
)

__all__ = [
    "DOORBELL_MAX_TRIGGER_CHARS",
    "DoorbellValidationError",
    "deliver_doorbell_trigger",
    "is_doorbell_trigger",
    "resolve_king_terminal_id",
    "validate_doorbell_trigger",
]
