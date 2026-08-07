"""D16: get_terminal_transcript MCP capability gate (fail-closed)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.mcp_server import server as srv
from cli_agent_orchestrator.mcp_server.server import (
    _caller_has_get_terminal_transcript_capability,
    get_terminal_transcript,
)


class TestTranscriptCapabilityHelper:
    def test_rook_transcript_has_capability(self):
        assert _caller_has_get_terminal_transcript_capability("rook_transcript") is True

    def test_rook_does_not(self):
        assert _caller_has_get_terminal_transcript_capability("rook") is False

    def test_missing_profile_fails_closed(self):
        assert _caller_has_get_terminal_transcript_capability("no-such-profile") is False

    def test_none_fails_closed(self):
        assert _caller_has_get_terminal_transcript_capability(None) is False


class TestGetTerminalTranscriptTool:
    def _ctx(self, profile: str) -> dict:
        return {
            "terminal_id": "aabbccdd",
            "session_name": "sess",
            "agent_profile": profile,
            "provider": "claude_code",
        }

    def test_missing_context_fails_closed(self):
        with patch.object(srv, "_get_terminal_context_from_env", return_value=None):
            result = asyncio.run(get_terminal_transcript(terminal_id="abcd1234"))
        assert result["success"] is False
        assert "terminal context" in result["error"]

    def test_without_capability_refused(self):
        with (
            patch.object(
                srv, "_get_terminal_context_from_env", return_value=self._ctx("developer")
            ),
            patch.object(srv, "_caller_has_get_terminal_transcript_capability", return_value=False),
        ):
            result = asyncio.run(get_terminal_transcript(terminal_id="abcd1234"))
        assert result["success"] is False
        assert "not authorized" in result["error"]

    def test_with_capability_calls_http_route(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "output": "tail-end",
            "truncated": True,
            "total_chars": 20,
            "terminal_id": "abcd1234",
            "source": "log",
        }
        with (
            patch.object(
                srv,
                "_get_terminal_context_from_env",
                return_value=self._ctx("rook_transcript"),
            ),
            patch.object(srv, "_caller_has_get_terminal_transcript_capability", return_value=True),
            patch(
                "cli_agent_orchestrator.mcp_server.server.requests.get", return_value=mock_resp
            ) as mock_get,
            patch("cli_agent_orchestrator.mcp_server.utils._auth_headers", return_value={}),
        ):
            result = asyncio.run(get_terminal_transcript(terminal_id="abcd1234", max_chars=8))

        assert result["success"] is True
        assert result["output"] == "tail-end"
        assert result["source"] == "log"
        assert result["truncated"] is True
        called_url = mock_get.call_args.args[0]
        assert called_url.endswith("/terminals/abcd1234/transcript")
        assert mock_get.call_args.kwargs["params"] == {"max_chars": 8}
        # D16 King arbitration: MCP must forward caller TerminalId to HTTP.
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["X-CAO-Caller-Terminal-Id"] == "aabbccdd"

    def test_forwards_caller_header_with_auth_headers(self):
        """Caller TerminalId is merged with _auth_headers(), not replaced."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "output": "x",
            "truncated": False,
            "total_chars": 1,
            "terminal_id": "abcd1234",
            "source": "log",
        }
        with (
            patch.object(
                srv,
                "_get_terminal_context_from_env",
                return_value=self._ctx("rook_transcript"),
            ),
            patch.object(srv, "_caller_has_get_terminal_transcript_capability", return_value=True),
            patch(
                "cli_agent_orchestrator.mcp_server.server.requests.get", return_value=mock_resp
            ) as mock_get,
            patch(
                "cli_agent_orchestrator.mcp_server.utils._auth_headers",
                return_value={"Authorization": "Bearer tok"},
            ),
        ):
            result = asyncio.run(get_terminal_transcript(terminal_id="abcd1234"))

        assert result["success"] is True
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok"
        assert headers["X-CAO-Caller-Terminal-Id"] == "aabbccdd"
