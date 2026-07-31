"""D6: spawn-depth gate on assign/handoff."""

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.mcp_server.server import (
    HandoffContext,
    _assign_impl,
    _child_agent_depth_or_reject,
    _handoff_impl,
    _parent_agent_depth,
)


def _ctx(provider="claude_code"):
    return HandoffContext(
        provider=provider,
        session_name="cao-s1",
        caller_id="a1b2c3d4",
        allowed_tools=None,
    )


class TestParentAgentDepth:
    def test_absent_means_zero(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CAO_AGENT_DEPTH", None)
            assert _parent_agent_depth() == 0

    def test_reads_env(self):
        with patch.dict(os.environ, {"CAO_AGENT_DEPTH": "2"}):
            assert _parent_agent_depth() == 2

    def test_invalid_falls_back_to_zero(self):
        with patch.dict(os.environ, {"CAO_AGENT_DEPTH": "nope"}):
            assert _parent_agent_depth() == 0


class TestChildDepthOrReject:
    def test_allows_within_cap(self, monkeypatch):
        # Default max 3: supervisor(0)→planner(1)→worker(2) allowed.
        monkeypatch.setenv("CAO_AGENT_DEPTH", "0")
        monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.CAO_MAX_AGENT_DEPTH", 3)
        assert _child_agent_depth_or_reject() == 1

    def test_allows_worker_depth(self, monkeypatch):
        # Parent at 1 → child at 2 (worker) is still under the cap.
        monkeypatch.setenv("CAO_AGENT_DEPTH", "1")
        monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.CAO_MAX_AGENT_DEPTH", 3)
        assert _child_agent_depth_or_reject() == 2

    def test_rejects_when_child_would_reach_cap(self, monkeypatch):
        # Parent at 2 → child would be 3, which reaches CAO_MAX_AGENT_DEPTH=3.
        monkeypatch.setenv("CAO_AGENT_DEPTH", "2")
        monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.CAO_MAX_AGENT_DEPTH", 3)
        err = _child_agent_depth_or_reject()
        assert isinstance(err, str)
        assert "Spawn depth limit reached" in err
        assert "Do the work yourself" in err
        assert "child depth would be 3" in err


class TestAssignDepthGate:
    def test_assign_rejects_when_child_would_reach_cap(self, monkeypatch):
        monkeypatch.setenv("CAO_AGENT_DEPTH", "2")
        monkeypatch.setenv("CAO_TERMINAL_ID", "a1b2c3d4")
        monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.CAO_MAX_AGENT_DEPTH", 3)
        result = _assign_impl("developer", "do stuff")
        assert result["success"] is False
        assert result["terminal_id"] is None
        assert "Spawn depth limit reached" in result["message"]

    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server._get_cleanup_nudge", return_value="")
    def test_assign_injects_child_depth(self, _nudge, mock_create, monkeypatch):
        monkeypatch.setenv("CAO_AGENT_DEPTH", "1")
        monkeypatch.setenv("CAO_TERMINAL_ID", "a1b2c3d4")
        monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.CAO_MAX_AGENT_DEPTH", 3)
        mock_create.return_value = ("deadbeef", "claude_code")
        result = _assign_impl("developer", "do stuff")
        assert result["success"] is True
        assert mock_create.call_args.kwargs["env_vars"] == {"CAO_AGENT_DEPTH": "2"}


class TestHandoffDepthGate:
    def test_handoff_rejects_when_child_would_reach_cap(self, monkeypatch):
        monkeypatch.setenv("CAO_AGENT_DEPTH", "2")
        monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.CAO_MAX_AGENT_DEPTH", 3)
        result = asyncio.run(_handoff_impl("developer", "do stuff"))
        assert result.success is False
        assert result.terminal_id is None
        assert "Spawn depth limit reached" in result.message

    @patch("cli_agent_orchestrator.mcp_server.server._get_cleanup_nudge", return_value="")
    @patch("cli_agent_orchestrator.mcp_server.server._resolve_handoff_provider")
    def test_handoff_injects_child_depth(self, mock_provider, _nudge, monkeypatch):
        monkeypatch.setenv("CAO_AGENT_DEPTH", "0")
        monkeypatch.setenv("CAO_TERMINAL_ID", "a1b2c3d4")
        monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.CAO_MAX_AGENT_DEPTH", 3)
        mock_provider.return_value = _ctx()
        with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "terminal_id": "deadbeef",
                "last_message": "done",
                "status": "completed",
            }
            resp.raise_for_status.return_value = None
            mock_requests.post.return_value = resp
            mock_requests.Timeout = Exception
            result = asyncio.run(_handoff_impl("developer", "do stuff"))
        assert result.success is True
        payload = mock_requests.post.call_args.kwargs["json"]
        assert payload["env_vars"] == {"CAO_AGENT_DEPTH": "1"}
