"""Unit tests for doorbell trigger validation."""

import pytest

from cli_agent_orchestrator.doorbell.validation import (
    DOORBELL_MAX_TRIGGER_CHARS,
    DoorbellValidationError,
    is_doorbell_trigger,
    parse_doorbell_trigger,
    validate_doorbell_trigger,
)


class TestValidateDoorbellTrigger:
    def test_accepts_canonical_example(self):
        trigger = "[github:pr_checks:org/repo#42] failing"
        assert validate_doorbell_trigger(trigger) == trigger

    def test_strips_leading_whitespace(self):
        trigger = "[github:pr_checks:org/repo#42] failing"
        assert validate_doorbell_trigger(f"  {trigger}") == trigger

    def test_trims_trailing_whitespace_on_hint(self):
        trigger = "[github:pr_checks:org/repo#42] failing"
        assert validate_doorbell_trigger(f"{trigger}  ") == trigger

    def test_parses_structured_fields(self):
        parsed = parse_doorbell_trigger("[ci:workflow_run:1234567890] failed")
        assert parsed.source == "ci"
        assert parsed.trigger_type == "workflow_run"
        assert parsed.identifier == "1234567890"
        assert parsed.hint == "failed"

    def test_rejects_over_200_chars_without_truncation(self):
        long_hint = "x" * (DOORBELL_MAX_TRIGGER_CHARS - 10)
        trigger = f"[github:pr_checks:org/repo#42] {long_hint}"
        assert len(trigger) > DOORBELL_MAX_TRIGGER_CHARS
        with pytest.raises(DoorbellValidationError, match="never truncated"):
            validate_doorbell_trigger(trigger)

    def test_rejects_newline(self):
        with pytest.raises(DoorbellValidationError, match="single line"):
            validate_doorbell_trigger("[github:pr_checks:org/repo#42] fail\nextra")

    def test_rejects_control_characters(self):
        with pytest.raises(DoorbellValidationError, match="control characters"):
            validate_doorbell_trigger("[github:pr_checks:org/repo#42] fail\x07")

    def test_rejects_whitespace_only_hint(self):
        with pytest.raises(DoorbellValidationError, match="hint must not be empty"):
            validate_doorbell_trigger("[github:pr_checks:org/repo#42]    ")

    def test_rejects_malformed_header(self):
        with pytest.raises(DoorbellValidationError, match="must match"):
            validate_doorbell_trigger("github:pr_checks:org/repo#42 failing")

    def test_rejects_empty_trigger(self):
        with pytest.raises(DoorbellValidationError, match="must not be empty"):
            validate_doorbell_trigger("   ")

    def test_is_doorbell_trigger_matches_validator(self):
        good = "[github:pr_checks:org/repo#42] failing"
        assert is_doorbell_trigger(good) is True
        assert is_doorbell_trigger(good + "\n") is False
        assert is_doorbell_trigger("[bad]") is False
