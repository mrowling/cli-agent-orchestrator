"""Shared fixtures for MCP server unit tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _wave_admit_passthrough(monkeypatch):
    """ADT-6 default: wave concurrency admits immediately (under-cap path).

    Wave-specific tests override ``wave_client.try_admit`` / wait / release.
    """

    def _admit(supervisor_id, kind, payload=None):
        return {
            "status": "admitted",
            "reservation_id": f"wres-test-{kind}",
            "queue_id": None,
            "position": None,
            "terminal_id": None,
            "message": "test admit",
        }

    monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.wave_client.try_admit", _admit)
    monkeypatch.setattr(
        "cli_agent_orchestrator.mcp_server.server.wave_client.bind_terminal",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.mcp_server.server.wave_client.release",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.mcp_server.server.wave_client.requeue_after_global_cap",
        lambda *a, **k: {"queue_id": "wque-requeue", "status": "queued"},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.mcp_server.server.wave_client.wait_for_admission",
        lambda queue_id, timeout=None: {
            "status": "admitted",
            "reservation_id": "wres-waited",
            "queue_id": queue_id,
            "terminal_id": None,
            "message": "test wait",
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.mcp_server.server.wave_client.cancel_request",
        lambda *a, **k: None,
    )


@pytest.fixture(autouse=True)
def _assign_supervisor_metadata(monkeypatch):
    """Stub supervisor terminal metadata GET used by ``_assign_impl`` wave payload.

    Only affects calls that look like GET /terminals/{id} (not working-directory).
    Tests that fully mock ``server.requests`` remain unaffected when they
    replace the whole module attribute after this fixture runs — those tests
    own their mocks. For ``_assign_impl`` tests that only mock ``_create_terminal``,
    this supplies the metadata needed before admit/create.
    """
    real_get = None
    try:
        import cli_agent_orchestrator.mcp_server.server as server_mod

        real_get = server_mod.requests.get
    except Exception:
        pass

    def _get(url, *args, **kwargs):
        url_s = str(url)
        if "/working-directory" in url_s:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"working_directory": "/tmp"}
            resp.raise_for_status.return_value = None
            return resp
        # Terminal metadata: /terminals/{8-hex} without trailing path segments
        if "/terminals/" in url_s and url_s.rstrip("/").count("/") >= 1:
            # Avoid hijacking unrelated GETs if a test uses a full mock later
            tid = url_s.rstrip("/").rsplit("/", 1)[-1]
            if len(tid) == 8 and all(c in "0123456789abcdef" for c in tid):
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "provider": "claude_code",
                    "session_name": "cao-test-session",
                    "allowed_tools": None,
                    "id": tid,
                }
                resp.raise_for_status.return_value = None
                return resp
        if real_get is not None:
            return real_get(url, *args, **kwargs)
        raise AssertionError(f"Unexpected GET in MCP tests: {url}")

    monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.requests.get", _get)
