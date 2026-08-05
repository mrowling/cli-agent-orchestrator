"""Tests for MCP server models."""

from cli_agent_orchestrator.mcp_server.models import HandoffResult


class TestHandoffResult:
    """Tests for HandoffResult model."""

    def test_create_successful_handoff(self):
        """Test creating a successful handoff result."""
        result = HandoffResult(
            success=True,
            message="Handoff completed successfully",
            output="Agent response",
            terminal_id="term-123",
        )

        assert result.success is True
        assert result.message == "Handoff completed successfully"
        assert result.output == "Agent response"
        assert result.terminal_id == "term-123"

    def test_create_failed_handoff(self):
        """Test creating a failed handoff result."""
        result = HandoffResult(
            success=False,
            message="Handoff failed: timeout",
        )

        assert result.success is False
        assert result.message == "Handoff failed: timeout"
        assert result.output is None
        assert result.terminal_id is None

    def test_handoff_result_optional_fields(self):
        """Test handoff result with optional fields."""
        result = HandoffResult(
            success=True,
            message="Partial success",
            output=None,
            terminal_id="term-456",
        )

        assert result.output is None
        assert result.terminal_id == "term-456"

    def test_handoff_result_model_dump(self):
        """Test handoff result model dump."""
        result = HandoffResult(
            success=True,
            message="Test",
            output="Output",
            terminal_id="term-789",
        )

        data = result.model_dump()
        assert data["success"] is True
        assert data["message"] == "Test"
        assert data["output"] == "Output"
        assert data["terminal_id"] == "term-789"
        assert data["duration_ms"] is None

    def test_handoff_result_with_duration_ms(self):
        """D2: duration_ms is surfaced on HandoffResult."""
        result = HandoffResult(
            success=True,
            message="Done",
            output="Output",
            terminal_id="term-001",
            duration_ms=1234,
        )

        assert result.duration_ms == 1234
        assert result.model_dump()["duration_ms"] == 1234

    def test_handoff_result_done_sentinel_fields_optional(self):
        """ADT-1: done_status/done_summary are nullable for backward compatibility."""
        result = HandoffResult(
            success=True,
            message="Done",
            output="Finished without sentinel",
            terminal_id="term-002",
        )

        assert result.done_status is None
        assert result.done_summary is None
        data = result.model_dump()
        assert data["done_status"] is None
        assert data["done_summary"] is None

    def test_handoff_result_done_cmd_fields_optional(self):
        """ADT-3: done_cmd audit fields are nullable for backward compatibility."""
        result = HandoffResult(
            success=True,
            message="Done",
            output="Finished",
            terminal_id="term-003",
        )

        assert result.done_cmd is None
        assert result.done_cmd_exit is None
        assert result.done_cmd_output is None
        assert result.done_cmd_timed_out is None
        assert result.done_cmd_error is None
