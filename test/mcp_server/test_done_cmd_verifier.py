"""Unit tests for handoff done_cmd verifier (ADT-3)."""

import subprocess
import sys
from unittest.mock import MagicMock, patch

from cli_agent_orchestrator.constants import DONE_CMD_OUTPUT_MAX_CHARS
from cli_agent_orchestrator.mcp_server.done_cmd_verifier import (
    _bound_output,
    run_done_cmd,
)


class TestBoundOutput:
    def test_short_output_unchanged(self):
        assert _bound_output("hello") == "hello"

    def test_long_output_tail_truncated(self):
        text = "x" * (DONE_CMD_OUTPUT_MAX_CHARS + 100)
        bounded = _bound_output(text)
        assert bounded.endswith("x" * DONE_CMD_OUTPUT_MAX_CHARS)
        assert "omitted first 100 chars" in bounded


class TestRunDoneCmd:
    def test_pass_python_exit_zero(self):
        result = run_done_cmd(
            f'{sys.executable} -c "import sys; sys.exit(0)"',
            cwd=None,
        )
        assert result.accepted is True
        assert result.exit_code == 0
        assert result.error is None
        assert result.timed_out is False

    def test_fail_python_exit_one(self):
        result = run_done_cmd(
            f'{sys.executable} -c "import sys; sys.exit(1)"',
            cwd=None,
        )
        assert result.accepted is False
        assert result.exit_code == 1

    def test_empty_argv_is_auditable_failure(self):
        result = run_done_cmd("   ", cwd=None)
        assert result.accepted is False
        assert result.error == "done_cmd produced empty argv after parsing"
        assert result.exit_code is None

    def test_parse_error_is_auditable_failure(self):
        result = run_done_cmd('"unclosed', cwd=None)
        assert result.accepted is False
        assert result.error is not None
        assert "parse error" in result.error

    @patch("cli_agent_orchestrator.mcp_server.done_cmd_verifier.subprocess.Popen")
    def test_timeout_sets_timed_out(self, mock_popen):
        proc = MagicMock()
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd=["sleep"], timeout=1)
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc
        with patch(
            "cli_agent_orchestrator.mcp_server.done_cmd_verifier._kill_process_tree"
        ) as kill:
            # After kill, communicate returns empty streams.
            proc.communicate.side_effect = [
                subprocess.TimeoutExpired(cmd=["sleep"], timeout=1),
                ("", ""),
            ]
            result = run_done_cmd("sleep 999", cwd="/tmp")
            kill.assert_called_once()
        assert result.accepted is False
        assert result.timed_out is True
        assert result.error is not None
        assert "timed out" in result.error

    @patch("cli_agent_orchestrator.mcp_server.done_cmd_verifier.subprocess.Popen")
    def test_spawn_error_is_auditable(self, mock_popen):
        mock_popen.side_effect = OSError("exec failed")
        result = run_done_cmd(f'{sys.executable} -c "pass"', cwd=None)
        assert result.accepted is False
        assert result.error is not None
        assert "spawn error" in result.error

    @patch("cli_agent_orchestrator.mcp_server.done_cmd_verifier.subprocess.Popen")
    def test_uses_worker_cwd(self, mock_popen):
        proc = MagicMock()
        proc.communicate.return_value = ("", "")
        proc.returncode = 0
        mock_popen.return_value = proc
        run_done_cmd("echo ok", cwd="/tmp/worker")
        assert mock_popen.call_args.kwargs["cwd"] == "/tmp/worker"
        assert mock_popen.call_args.kwargs["shell"] is False

    @patch("cli_agent_orchestrator.mcp_server.done_cmd_verifier.subprocess.Popen")
    def test_captures_stdout_and_stderr(self, mock_popen):
        proc = MagicMock()
        proc.communicate.return_value = ("out", "err")
        proc.returncode = 0
        mock_popen.return_value = proc
        result = run_done_cmd("cmd", cwd=None)
        assert result.output == "outerr"

    def test_timeout_kills_process_group(self, monkeypatch, tmp_path):
        """POSIX: timed-out done_cmd must kill the process group, not just the parent."""
        script = tmp_path / "sleepy.py"
        script.write_text(
            "import os, time, sys\n"
            "if os.fork() == 0:\n"
            "    time.sleep(60)\n"
            "    sys.exit(0)\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.done_cmd_verifier.DONE_CMD_TIMEOUT_SECONDS",
            1,
        )
        result = run_done_cmd(f"{sys.executable} {script}")
        assert result.timed_out is True
        assert result.accepted is False
