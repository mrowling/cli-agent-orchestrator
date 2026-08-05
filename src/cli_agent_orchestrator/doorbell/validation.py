"""Validate canonical doorbell trigger strings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Whole trigger, including header and hint, must fit in one inbox line.
DOORBELL_MAX_TRIGGER_CHARS = 200

# Field tokens are constrained to keep triggers safe for logs, inbox paste, and
# shell-adjacent tooling. No whitespace or control characters inside tokens.
# source/type: short lowercase-ish identifiers (github, pr_checks, …)
_FIELD_TOKEN_RE = r"[A-Za-z0-9._-]{1,32}"
# id: allows repo/path/issue refs such as org/repo#42 or run identifiers
_IDENTIFIER_TOKEN_RE = r"[A-Za-z0-9._:/#@-]{1,64}"

_DOORBELL_TRIGGER_RE = re.compile(
    rf"^\[{_FIELD_TOKEN_RE}:{_FIELD_TOKEN_RE}:{_IDENTIFIER_TOKEN_RE}\] .+$"
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class ParsedDoorbellTrigger:
    """Structured view of a validated doorbell trigger."""

    source: str
    trigger_type: str
    identifier: str
    hint: str
    raw: str


class DoorbellValidationError(ValueError):
    """Raised when a trigger fails canonical doorbell validation."""


def _contains_control_chars(value: str) -> bool:
    return _CONTROL_CHAR_RE.search(value) is not None


def is_doorbell_trigger(message: str) -> bool:
    """Return True when ``message`` matches the canonical doorbell trigger shape."""

    if not message or len(message) > DOORBELL_MAX_TRIGGER_CHARS:
        return False
    if _contains_control_chars(message) or "\n" in message or "\r" in message:
        return False
    return _DOORBELL_TRIGGER_RE.fullmatch(message.strip()) is not None


def validate_doorbell_trigger(trigger: str) -> str:
    """Validate and return the normalized trigger string.

    Canonical form: ``[source:type:id] hint`` (single line, ≤200 chars).

    Raises:
        DoorbellValidationError: When shape, length, or character policy fails.
    """

    if trigger is None:
        raise DoorbellValidationError("trigger is required")

    normalized = trigger.lstrip()
    if not normalized:
        raise DoorbellValidationError("trigger must not be empty")

    if len(normalized) > DOORBELL_MAX_TRIGGER_CHARS:
        raise DoorbellValidationError(
            f"trigger exceeds {DOORBELL_MAX_TRIGGER_CHARS} characters "
            f"({len(normalized)} given); shorten the hint — triggers are never truncated"
        )

    if "\n" in normalized or "\r" in normalized:
        raise DoorbellValidationError("trigger must be a single line (no newlines)")

    if _contains_control_chars(normalized):
        raise DoorbellValidationError(
            "trigger must not contain control characters; use printable text only"
        )

    match = _DOORBELL_TRIGGER_RE.fullmatch(normalized)
    if match is None:
        raise DoorbellValidationError(
            "trigger must match [source:type:id] hint — "
            "example: [github:pr_checks:org/repo#42] failing"
        )

    header, hint = normalized.split("] ", 1)
    hint = hint.rstrip()
    if not hint.strip():
        raise DoorbellValidationError("trigger hint must not be empty after '] '")

    return f"{header}] {hint}"


def parse_doorbell_trigger(trigger: str) -> ParsedDoorbellTrigger:
    """Parse a validated trigger into structured fields."""

    validated = validate_doorbell_trigger(trigger)
    header_body, hint = validated.split("] ", 1)
    source, trigger_type, identifier = header_body[1:].split(":", 2)
    return ParsedDoorbellTrigger(
        source=source,
        trigger_type=trigger_type,
        identifier=identifier,
        hint=hint,
        raw=validated,
    )
