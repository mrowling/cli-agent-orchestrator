"""D16: GET /terminals/{id}/transcript and auth on /output."""

from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.api.main import app
from cli_agent_orchestrator.security import auth

CALLER_CAPABLE = "aabbccdd"
CALLER_INCAPABLE = "11223344"
CALLER_HEADER = "X-CAO-Caller-Terminal-Id"


def _has_scope_dependency(route) -> bool:
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        call = getattr(dep, "call", None)
        if call is not None and "require_any_scope" in getattr(call, "__qualname__", ""):
            return True
        stack.extend(getattr(dep, "dependencies", []))
    return False


def _override_scopes(scopes):
    async def _dep():
        return list(scopes)

    return _dep


def _meta(profile: str) -> dict:
    return {"id": "x", "agent_profile": profile}


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "https://idp.example/jwks")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(auth.get_current_scopes, None)


@pytest.fixture
def capable_caller():
    """Patch DB + profile load so CALLER_CAPABLE has get_terminal_transcript."""

    def _get_meta(tid):
        if tid == CALLER_CAPABLE:
            return _meta("rook_transcript")
        if tid == CALLER_INCAPABLE:
            return _meta("rook")
        return None

    with (
        patch(
            "cli_agent_orchestrator.api.main.get_terminal_metadata",
            side_effect=_get_meta,
        ),
        patch("cli_agent_orchestrator.api.main.load_agent_profile") as mock_load,
    ):

        def _load(name):
            caps = ["get_terminal_transcript"] if name == "rook_transcript" else ["review"]
            return MagicMock(capabilities=caps, name=name)

        mock_load.side_effect = _load
        yield


class TestTranscriptRoute:
    def test_transcript_route_has_scope_dependency(self):
        route = next(
            r
            for r in app.routes
            if getattr(r, "path", None) == "/terminals/{terminal_id}/transcript"
            and "GET" in (r.methods or ())
        )
        assert _has_scope_dependency(route)

    def test_output_route_has_scope_dependency(self):
        """D16: close ungated hole on GET /terminals/{id}/output."""
        route = next(
            r
            for r in app.routes
            if getattr(r, "path", None) == "/terminals/{terminal_id}/output"
            and "GET" in (r.methods or ())
        )
        assert _has_scope_dependency(route)

    def test_no_caller_header_forbidden(self, client):
        """D16 King arbitration: auth-off must not dump transcripts without caller proof."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            response = client.get("/terminals/abcd1234/transcript")
        assert response.status_code == 403
        mock_svc.read_terminal_transcript.assert_not_called()

    def test_incapable_caller_profile_forbidden(self, client, capable_caller):
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            response = client.get(
                "/terminals/abcd1234/transcript",
                headers={CALLER_HEADER: CALLER_INCAPABLE},
            )
        assert response.status_code == 403
        mock_svc.read_terminal_transcript.assert_not_called()

    def test_capable_caller_profile_allowed(self, client, capable_caller):
        """rook_transcript capability → 200."""
        payload = {
            "output": "hello",
            "truncated": False,
            "total_chars": 5,
            "terminal_id": "abcd1234",
            "source": "log",
        }
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.read_terminal_transcript.return_value = payload
            response = client.get(
                "/terminals/abcd1234/transcript",
                headers={CALLER_HEADER: CALLER_CAPABLE},
            )

        assert response.status_code == 200
        assert response.json() == payload
        mock_svc.read_terminal_transcript.assert_called_once_with("abcd1234", None)

    def test_get_transcript_success(self, client, capable_caller):
        payload = {
            "output": "hello",
            "truncated": False,
            "total_chars": 5,
            "terminal_id": "abcd1234",
            "source": "log",
        }
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.read_terminal_transcript.return_value = payload
            response = client.get(
                "/terminals/abcd1234/transcript",
                headers={CALLER_HEADER: CALLER_CAPABLE},
            )

        assert response.status_code == 200
        assert response.json() == payload
        mock_svc.read_terminal_transcript.assert_called_once_with("abcd1234", None)

    def test_get_transcript_max_chars(self, client, capable_caller):
        payload = {
            "output": "tail",
            "truncated": True,
            "total_chars": 100,
            "terminal_id": "abcd1234",
            "source": "scrollback",
        }
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.read_terminal_transcript.return_value = payload
            response = client.get(
                "/terminals/abcd1234/transcript?max_chars=4",
                headers={CALLER_HEADER: CALLER_CAPABLE},
            )

        assert response.status_code == 200
        assert response.json()["truncated"] is True
        mock_svc.read_terminal_transcript.assert_called_once_with("abcd1234", 4)

    def test_get_transcript_not_found(self, client, capable_caller):
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.read_terminal_transcript.side_effect = ValueError(
                "No transcript found for terminal 'deadbeef'"
            )
            response = client.get(
                "/terminals/deadbeef/transcript",
                headers={CALLER_HEADER: CALLER_CAPABLE},
            )

        assert response.status_code == 404

    def test_invalid_terminal_id_rejected(self, client):
        # TerminalId pattern ^[a-f0-9]{8}$ — path escape attempts fail validation.
        response = client.get("/terminals/../etc/passwd/transcript")
        assert response.status_code in (404, 422)

    def test_unknown_caller_terminal_forbidden(self, client, capable_caller):
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            response = client.get(
                "/terminals/abcd1234/transcript",
                headers={CALLER_HEADER: "deadbeef"},
            )
        assert response.status_code == 403
        mock_svc.read_terminal_transcript.assert_not_called()

    def test_invalid_caller_header_forbidden(self, client):
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            response = client.get(
                "/terminals/abcd1234/transcript",
                headers={CALLER_HEADER: "not-hex!!"},
            )
        assert response.status_code == 403
        mock_svc.read_terminal_transcript.assert_not_called()

    def test_output_requires_auth_when_auth_on(self, client, auth_on):
        app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_output.return_value = "secret"
            response = client.get("/terminals/abcd1234/output")
        assert response.status_code == 403
        mock_svc.get_output.assert_not_called()

    def test_transcript_requires_auth_when_auth_on(self, client, auth_on, capable_caller):
        app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            response = client.get(
                "/terminals/abcd1234/transcript",
                headers={CALLER_HEADER: CALLER_CAPABLE},
            )
        assert response.status_code == 403
        mock_svc.read_terminal_transcript.assert_not_called()

    def test_output_admits_read_scope(self, client, auth_on):
        app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_output.return_value = "ok"
            response = client.get("/terminals/abcd1234/output")
        assert response.status_code == 200
