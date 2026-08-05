"""Tests for ===CAO_DONE=== sentinel parsing."""

from cli_agent_orchestrator.mcp_server.done_sentinel import parse_done_sentinel
from cli_agent_orchestrator.mcp_server.models import HandoffResult
from cli_agent_orchestrator.mcp_server.server import _handoff_result_with_done_sentinel


class TestParseDoneSentinel:
    """Unit tests for parse_done_sentinel final-valid contract."""

    def test_ok_sentinel(self):
        parsed = parse_done_sentinel(
            "Implemented feature.\n===CAO_DONE=== status=ok summary=Added login endpoint"
        )
        assert parsed is not None
        assert parsed.status == "ok"
        assert parsed.summary == "Added login endpoint"

    def test_fail_sentinel(self):
        parsed = parse_done_sentinel(
            "Could not compile.\n===CAO_DONE=== status=fail summary=Tests failed on auth module"
        )
        assert parsed is not None
        assert parsed.status == "fail"
        assert parsed.summary == "Tests failed on auth module"

    def test_blocked_sentinel(self):
        parsed = parse_done_sentinel(
            "NEED: push abc main\n===CAO_DONE=== status=blocked summary=Waiting for host push"
        )
        assert parsed is not None
        assert parsed.status == "blocked"
        assert parsed.summary == "Waiting for host push"

    def test_missing_sentinel(self):
        assert parse_done_sentinel("All done, shipped the fix.") is None

    def test_empty_capture(self):
        assert parse_done_sentinel("") is None
        assert parse_done_sentinel(None) is None

    def test_malformed_status_ignored(self):
        parsed = parse_done_sentinel(
            "===CAO_DONE=== status=ok summary=First valid\n"
            "===CAO_DONE=== status=unknown summary=Bad trailing noise"
        )
        assert parsed is not None
        assert parsed.status == "ok"
        assert parsed.summary == "First valid"

    def test_malformed_missing_summary_ignored(self):
        parsed = parse_done_sentinel(
            "===CAO_DONE=== status=ok summary=Kept\n===CAO_DONE=== status=fail"
        )
        assert parsed is not None
        assert parsed.status == "ok"
        assert parsed.summary == "Kept"

    def test_final_valid_wins_over_earlier(self):
        parsed = parse_done_sentinel(
            "===CAO_DONE=== status=fail summary=First attempt\n"
            "Retried successfully.\n"
            "===CAO_DONE=== status=ok summary=Fixed on second pass"
        )
        assert parsed is not None
        assert parsed.status == "ok"
        assert parsed.summary == "Fixed on second pass"

    def test_multiline_noise_before_and_after(self):
        output = "\n".join(
            [
                "Starting work...",
                "Running tests...",
                "All green.",
                "",
                "===CAO_DONE=== status=ok summary=Tests pass locally",
                "accidental trailing prose must not match",
            ]
        )
        parsed = parse_done_sentinel(output)
        assert parsed is not None
        assert parsed.status == "ok"
        assert parsed.summary == "Tests pass locally"

    def test_sentinel_must_be_own_line(self):
        assert parse_done_sentinel("Done now ===CAO_DONE=== status=ok summary=inline") is None

    def test_summary_allows_punctuation(self):
        parsed = parse_done_sentinel(
            "===CAO_DONE=== status=ok summary=Updated 3 files; no follow-ups"
        )
        assert parsed is not None
        assert parsed.summary == "Updated 3 files; no follow-ups"

    def test_leading_trailing_whitespace_on_line(self):
        parsed = parse_done_sentinel("  ===CAO_DONE=== status=ok summary=Trimmed line  ")
        assert parsed is not None
        assert parsed.status == "ok"
        assert parsed.summary == "Trimmed line"


class TestHandoffResultDoneSentinelFields:
    """HandoffResult enrichment preserves backward compatibility."""

    def test_populates_done_fields_when_sentinel_present(self):
        result = _handoff_result_with_done_sentinel(
            success=True,
            message="ok",
            output="===CAO_DONE=== status=ok summary=Shipped",
            terminal_id="abc12345",
            duration_ms=100,
        )
        assert result.success is True
        assert result.done_status == "ok"
        assert result.done_summary == "Shipped"

    def test_missing_sentinel_leaves_fields_null(self):
        result = _handoff_result_with_done_sentinel(
            success=True,
            message="ok",
            output="Finished without sentinel",
            terminal_id="abc12345",
            duration_ms=100,
        )
        assert result.success is True
        assert result.done_status is None
        assert result.done_summary is None

    def test_model_dump_includes_nullable_done_fields(self):
        result = HandoffResult(
            success=True,
            message="Done",
            output="output",
            terminal_id="term-001",
        )
        data = result.model_dump()
        assert data["done_status"] is None
        assert data["done_summary"] is None
