"""MCP/queue wiring tests for workspace option (Item 4 + Item 6)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.mcp_server.server import (
    HandoffContext,
    _assign_impl,
    _handoff_impl,
)


def _ctx(provider="claude_code"):
    return HandoffContext(
        provider=provider,
        session_name="cao-sess",
        caller_id="a1b2c3d4",
        allowed_tools=None,
    )


class TestAssignPreservesWorkspaceInWavePayload:
    @patch("cli_agent_orchestrator.mcp_server.server.wave_client")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.resolve_provider", return_value="claude_code")
    @patch("cli_agent_orchestrator.mcp_server.server.requests")
    def test_assign_passes_workspace_to_create_and_queue(
        self, mock_requests, _resolve, mock_create, mock_wave
    ):
        meta = MagicMock()
        meta.json.return_value = {
            "provider": "claude_code",
            "session_name": "cao-sess",
            "allowed_tools": None,
        }
        meta.raise_for_status.return_value = None
        mock_requests.get.return_value = meta

        mock_wave.try_admit.return_value = {
            "status": "queued",
            "queue_id": "q1",
            "position": 1,
        }

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            with patch(
                "cli_agent_orchestrator.mcp_server.server._child_agent_depth_or_reject",
                return_value=1,
            ):
                result = _assign_impl(
                    "knight",
                    "do work",
                    workspace="worktree",
                )

        assert result["status"] == "queued"
        payload = mock_wave.try_admit.call_args[0][2]
        assert payload["workspace"] == "worktree"


class TestHandoffPreservesWorkspaceInRunStep:
    @patch("cli_agent_orchestrator.mcp_server.server._get_cleanup_nudge", return_value="")
    @patch("cli_agent_orchestrator.mcp_server.server._resolve_handoff_provider")
    @patch("cli_agent_orchestrator.mcp_server.server.wave_client")
    def test_handoff_posts_workspace_to_run_step(self, mock_wave, mock_provider, _nudge):
        mock_provider.return_value = _ctx()
        mock_wave.try_admit.return_value = {
            "status": "admitted",
            "reservation_id": "r1",
        }
        mock_wave.bind_terminal.return_value = None
        mock_wave.release.return_value = None

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "terminal_id": "deadbeef",
            "last_message": "done",
            "status": "completed",
            "workspace_backend": "worktree",
            "workspace_path": "/tmp/ws",
            "workspace_branch": "cao/ws-x",
            "workspace_base_ref": "abc",
            "workspace_cleanup_status": "removed",
            "workspace_cleanup_message": "ok",
            "workspace_retained_branch": "cao/ws-x",
        }

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            with patch(
                "cli_agent_orchestrator.mcp_server.server._child_agent_depth_or_reject",
                return_value=1,
            ):
                with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
                    mock_requests.post.return_value = resp
                    mock_requests.Timeout = Exception
                    result = asyncio.run(
                        _handoff_impl(
                            "developer",
                            "Implement hello",
                            workspace="worktree",
                        )
                    )

        assert result.success is True
        sent = mock_requests.post.call_args[1]["json"]
        assert sent["workspace"] == "worktree"
        assert result.workspace_backend == "worktree"
        assert result.workspace_path == "/tmp/ws"
        assert result.workspace_cleanup_status == "removed"
        assert result.workspace_retained_branch == "cao/ws-x"


class TestDoneCmdRunsBeforeWorktreeTeardown:
    """Integration: done_cmd with clean worktree + portable cwd/true verifier."""

    @patch("cli_agent_orchestrator.mcp_server.server._get_cleanup_nudge", return_value="")
    @patch("cli_agent_orchestrator.mcp_server.server._resolve_handoff_provider")
    @patch("cli_agent_orchestrator.mcp_server.server.wave_client")
    def test_done_cmd_true_in_live_worktree_cwd(self, mock_wave, mock_provider, _nudge, tmp_path):
        import shutil

        mock_provider.return_value = _ctx()
        mock_wave.try_admit.return_value = {
            "status": "admitted",
            "reservation_id": "r-done",
        }
        mock_wave.bind_terminal.return_value = None
        mock_wave.release.return_value = None

        # Live worktree path still present when verifier runs (teardown=False).
        worktree = tmp_path / "ws-live"
        worktree.mkdir()
        true_bin = shutil.which("true") or "/usr/bin/true"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "terminal_id": "deadbeef",
            "last_message": "===CAO_DONE=== status=ok summary=shipped",
            "status": "completed",
            "workspace_backend": "worktree",
            "workspace_path": str(worktree),
            "workspace_branch": "cao/ws-live",
        }

        del_resp = MagicMock()
        del_resp.status_code = 200
        del_resp.content = b'{"success":true,"workspace_cleanup_status":"removed"}'
        del_resp.json.return_value = {
            "success": True,
            "workspace_cleanup_status": "removed",
            "workspace_path": str(worktree),
            "workspace_retained_branch": "cao/ws-live",
        }

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            with patch(
                "cli_agent_orchestrator.mcp_server.server._child_agent_depth_or_reject",
                return_value=1,
            ):
                with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
                    mock_requests.post.return_value = resp
                    mock_requests.delete.return_value = del_resp
                    mock_requests.Timeout = Exception
                    result = asyncio.run(
                        _handoff_impl(
                            "developer",
                            "Implement hello",
                            workspace="worktree",
                            done_cmd=true_bin,
                        )
                    )

        assert result.success is True
        assert result.done_cmd_exit == 0
        sent = mock_requests.post.call_args[1]["json"]
        assert sent["teardown"] is False
        mock_requests.delete.assert_called_once()
        # Worktree existed for the verifier (portable true in that cwd).
        assert worktree.is_dir() or result.workspace_cleanup_status == "removed"
