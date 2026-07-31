"""D17: stacked reviewer profiles load and supervisor cycle text."""

from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile


class TestD17ReviewerProfiles:
    def test_reviewer_unchanged_no_transcript_capability(self):
        profile = load_agent_profile("reviewer")
        assert profile.role == "reviewer"
        caps = profile.capabilities or []
        assert "get_terminal_transcript" not in caps

    def test_reviewer_transcript_loads_with_capability(self):
        profile = load_agent_profile("reviewer_transcript")
        assert profile.name == "reviewer_transcript"
        assert profile.role == "reviewer"
        assert "get_terminal_transcript" in (profile.capabilities or [])

    def test_reviewer_adversarial_loads_without_pinned_model(self):
        profile = load_agent_profile("reviewer_adversarial")
        assert profile.name == "reviewer_adversarial"
        assert profile.role == "reviewer"
        # D17: decorrelation via per-call model override, not frontmatter pin.
        assert profile.model is None
        assert "get_terminal_transcript" not in (profile.capabilities or [])

    def test_code_supervisor_mandates_stacked_cycle(self):
        profile = load_agent_profile("code_supervisor")
        body = profile.system_prompt or ""
        assert "reviewer_transcript" in body
        assert "reviewer_adversarial" in body
        assert "get_terminal_transcript" in body
        assert "model=" in body
        assert "stacked review" in body.lower() or "stacked review cycle" in body.lower()
