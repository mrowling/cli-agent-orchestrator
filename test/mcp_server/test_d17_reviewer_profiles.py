"""Stacked rook review profiles load and supervisor cycle text."""

import pytest

from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile


@pytest.fixture(autouse=True)
def _isolate_from_user_agent_store(tmp_path, monkeypatch):
    """Load built-ins only — ignore ~/.cli-agent-orchestrator/agent-store."""
    import cli_agent_orchestrator.utils.agent_profiles as ap

    empty = tmp_path / "agent-store"
    empty.mkdir()
    monkeypatch.setattr(ap, "LOCAL_AGENT_STORE_DIR", empty)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.get_agent_dirs", lambda: {}
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.get_disabled_agent_dirs",
        lambda: [],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
        lambda: [],
    )


class TestRookReviewProfiles:
    def test_rook_loads_with_model(self):
        profile = load_agent_profile("rook")
        assert profile.role == "reviewer"
        assert profile.model == "cursor-grok-4.5-high"
        caps = profile.capabilities or []
        assert "get_terminal_transcript" not in caps

    def test_rook_transcript_loads_with_capability(self):
        profile = load_agent_profile("rook_transcript")
        assert profile.name == "rook_transcript"
        assert profile.role == "reviewer"
        assert "get_terminal_transcript" in (profile.capabilities or [])

    def test_rook_adversarial_loads_with_model(self):
        profile = load_agent_profile("rook-adversarial")
        assert profile.name == "rook-adversarial"
        assert profile.role == "reviewer"
        assert profile.model == "cursor-grok-4.5-high"
        assert "get_terminal_transcript" not in (profile.capabilities or [])

    def test_legacy_code_reviewer_profiles_removed(self):
        for name in ("reviewer", "reviewer_adversarial", "reviewer_transcript"):
            with pytest.raises(FileNotFoundError):
                load_agent_profile(name)

    def test_code_supervisor_mandates_stacked_cycle(self):
        profile = load_agent_profile("code_supervisor")
        body = profile.system_prompt or ""
        assert "rook_transcript" in body
        assert "rook-adversarial" in body
        assert "get_terminal_transcript" in body
        assert "stacked review" in body.lower() or "stacked review cycle" in body.lower()
        # code lens uses rook names, not the removed profile pair
        assert "agent_name: reviewer)" not in body
        assert "agent_name: reviewer_adversarial" not in body
        assert "handoff to `reviewer`" not in body
        assert "handoff to `reviewer_adversarial`" not in body
        assert "handoff to `reviewer_transcript`" not in body
