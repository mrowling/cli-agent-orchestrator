"""D2/D3: handoff returns duration_ms and records step.duration telemetry."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from cli_agent_orchestrator.mcp_server.server import HandoffContext, _handoff_impl


def _ctx(provider: str) -> HandoffContext:
    return HandoffContext(
        provider=provider,
        session_name="cao-test",
        caller_id="sup12345",
        allowed_tools=None,
    )


def _ok_run_step_response(terminal_id: str = "dev-term", last_message: str = "done") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "terminal_id": terminal_id,
        "last_message": last_message,
        "status": "completed",
    }
    return resp


class TestHandoffDuration:
    @patch("cli_agent_orchestrator.mcp_server.server._record_handoff_step_duration")
    @patch("cli_agent_orchestrator.mcp_server.server._get_cleanup_nudge", return_value="")
    @patch("cli_agent_orchestrator.mcp_server.server._resolve_handoff_provider")
    def test_success_returns_duration_ms(self, mock_provider, _nudge, mock_record):
        mock_provider.return_value = _ctx("kiro_cli")

        with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
            mock_requests.post.return_value = _ok_run_step_response()
            mock_requests.Timeout = Exception

            result = asyncio.run(_handoff_impl("developer", "Do task"))

        assert result.success is True
        assert result.duration_ms is not None
        assert result.duration_ms >= 0
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["outcome"] == "success"
        assert call_kwargs["agent_profile"] == "developer"
        assert call_kwargs["provider"] == "kiro_cli"

    @patch("cli_agent_orchestrator.mcp_server.server._record_handoff_step_duration")
    @patch("cli_agent_orchestrator.mcp_server.server._resolve_handoff_provider")
    def test_failure_returns_duration_ms(self, mock_provider, mock_record):
        mock_provider.return_value = _ctx("kiro_cli")

        with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
            resp = MagicMock()
            resp.status_code = 504
            resp.json.return_value = {
                "detail": {"message": "timed out", "kind": "timeout", "terminal_id": "abc12345"}
            }
            mock_requests.post.return_value = resp
            mock_requests.Timeout = Exception

            result = asyncio.run(_handoff_impl("developer", "Do task", timeout=30))

        assert result.success is False
        assert result.duration_ms is not None
        assert mock_record.call_args.kwargs["outcome"] == "timeout"

    @patch("cli_agent_orchestrator.mcp_server.server._record_handoff_step_duration")
    @patch("cli_agent_orchestrator.mcp_server.server._resolve_handoff_provider")
    def test_exception_returns_duration_ms(self, mock_provider, mock_record):
        mock_provider.side_effect = RuntimeError("resolution failed")

        with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
            mock_requests.Timeout = Exception
            result = asyncio.run(_handoff_impl("developer", "Do task"))

        assert result.success is False
        assert result.duration_ms is not None
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["outcome"] == "failure"
        assert call_kwargs["agent_profile"] == "developer"
        assert call_kwargs["provider"] == "unknown"
