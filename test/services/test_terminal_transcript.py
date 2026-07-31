"""D16: on-disk terminal transcript reader (not OutputMode.FULL)."""

from unittest.mock import patch

import pytest

from cli_agent_orchestrator.services import terminal_service


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    d = tmp_path / "terminal"
    d.mkdir()
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", d)
    return d


class TestReadTerminalTranscript:
    def test_prefers_log_over_scrollback(self, log_dir):
        tid = "abcd1234"
        (log_dir / f"{tid}.log").write_text("from-log", encoding="utf-8")
        (log_dir / f"{tid}.scrollback").write_text("from-scrollback", encoding="utf-8")

        result = terminal_service.read_terminal_transcript(tid)

        assert result["source"] == "log"
        assert result["output"] == "from-log"
        assert result["truncated"] is False
        assert result["total_chars"] == len("from-log")
        assert result["terminal_id"] == tid

    def test_falls_back_to_scrollback(self, log_dir):
        tid = "deadbeef"
        (log_dir / f"{tid}.scrollback").write_text("scroll-only", encoding="utf-8")

        result = terminal_service.read_terminal_transcript(tid)

        assert result["source"] == "scrollback"
        assert result["output"] == "scroll-only"

    def test_missing_both_files_raises(self, log_dir):
        with pytest.raises(ValueError, match="No transcript found"):
            terminal_service.read_terminal_transcript("00000000")

    def test_max_chars_tail_semantics(self, log_dir):
        tid = "cafe0001"
        body = "ABCDEFGHIJ"  # 10 chars
        (log_dir / f"{tid}.log").write_text(body, encoding="utf-8")

        result = terminal_service.read_terminal_transcript(tid, max_chars=4)

        assert result["output"] == "GHIJ"
        assert result["truncated"] is True
        assert result["total_chars"] == 10

    def test_max_chars_zero_or_none_is_no_cap(self, log_dir):
        tid = "cafe0002"
        body = "short"
        (log_dir / f"{tid}.log").write_text(body, encoding="utf-8")

        assert terminal_service.read_terminal_transcript(tid, max_chars=None)["output"] == body
        assert terminal_service.read_terminal_transcript(tid, max_chars=0)["output"] == body
        assert terminal_service.read_terminal_transcript(tid, max_chars=-1)["output"] == body

    def test_path_escape_via_unsafe_id_fails_closed(self, log_dir):
        """IDs with separators / traversal must not resolve outside TERMINAL_LOG_DIR."""
        with pytest.raises(ValueError):
            terminal_service.read_terminal_transcript("../etc/passwd")

        with pytest.raises(ValueError):
            terminal_service.read_terminal_transcript("abcd1234/../../x")

    def test_does_not_call_get_output_or_tmux(self, log_dir):
        tid = "feedface"
        (log_dir / f"{tid}.log").write_text("disk", encoding="utf-8")

        with (
            patch.object(terminal_service, "get_output") as mock_go,
            patch.object(terminal_service, "get_backend") as mock_backend,
        ):
            result = terminal_service.read_terminal_transcript(tid)

        assert result["output"] == "disk"
        mock_go.assert_not_called()
        mock_backend.assert_not_called()
