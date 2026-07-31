"""D7: CAO_MAX_ACTIVE_TERMINALS admission control in create_terminal."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.services.terminal_service import (
    TerminalCapacityError,
    create_terminal,
)


@pytest.mark.asyncio
@patch("cli_agent_orchestrator.clients.database.list_terminals_by_session")
@patch("cli_agent_orchestrator.services.terminal_service.get_session_env", return_value={})
@patch("cli_agent_orchestrator.services.terminal_service.status_monitor")
@patch("cli_agent_orchestrator.services.terminal_service.fifo_manager")
@patch("cli_agent_orchestrator.services.terminal_service.FIFO_DIR")
@patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
@patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
@patch("cli_agent_orchestrator.backends.registry._backend")
@patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
@patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
@patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
@patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
async def test_rejects_when_session_at_cap(
    mock_load_profile,
    mock_gen_id,
    mock_gen_session,
    mock_gen_window,
    mock_tmux,
    mock_db_create,
    mock_provider_manager,
    mock_fifo_dir,
    mock_fifo_manager,
    mock_status_monitor,
    mock_get_session_env,
    mock_list_terminals,
    monkeypatch,
):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.CAO_MAX_ACTIVE_TERMINALS", 12
    )
    mock_list_terminals.return_value = [{"id": f"t{i:07d}"} for i in range(12)]
    mock_load_profile.return_value = AgentProfile(name="developer", description="d")
    mock_gen_id.return_value = "test1234"
    mock_gen_window.return_value = "developer-abcd"
    mock_tmux.session_exists.return_value = True

    with pytest.raises(TerminalCapacityError, match="Terminal capacity reached") as exc_info:
        await create_terminal(
            "kiro_cli",
            "developer",
            session_name="cao-busy",
            new_session=False,
        )

    assert (
        "Retry" in str(exc_info.value)
        or "retry" in str(exc_info.value).lower()
        or ("Delete unused" in str(exc_info.value))
    )
    mock_tmux.create_window.assert_not_called()


@pytest.mark.asyncio
@patch("cli_agent_orchestrator.clients.database.list_terminals_by_session")
@patch("cli_agent_orchestrator.services.terminal_service.get_session_env", return_value={})
@patch("cli_agent_orchestrator.services.terminal_service.status_monitor")
@patch("cli_agent_orchestrator.services.terminal_service.fifo_manager")
@patch("cli_agent_orchestrator.services.terminal_service.FIFO_DIR")
@patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
@patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
@patch("cli_agent_orchestrator.backends.registry._backend")
@patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
@patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
@patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
@patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
async def test_allows_under_cap(
    mock_load_profile,
    mock_gen_id,
    mock_gen_session,
    mock_gen_window,
    mock_tmux,
    mock_db_create,
    mock_provider_manager,
    mock_fifo_dir,
    mock_fifo_manager,
    mock_status_monitor,
    mock_get_session_env,
    mock_list_terminals,
    monkeypatch,
):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.CAO_MAX_ACTIVE_TERMINALS", 12
    )
    mock_list_terminals.return_value = [{"id": f"t{i:07d}"} for i in range(11)]
    mock_load_profile.return_value = AgentProfile(name="developer", description="d")
    mock_gen_id.return_value = "test1234"
    mock_gen_window.return_value = "developer-abcd"
    mock_tmux.session_exists.return_value = True
    mock_tmux.create_window.return_value = "developer-abcd"
    mock_provider = AsyncMock()
    mock_provider.initialize.return_value = True
    mock_provider_manager.create_provider.return_value = mock_provider
    mock_fifo_dir.__truediv__ = MagicMock(return_value="fake.fifo")

    result = await create_terminal(
        "kiro_cli",
        "developer",
        session_name="cao-ok",
        new_session=False,
        env_vars={"CAO_AGENT_DEPTH": "1"},
    )
    assert result.id == "test1234"
    extra_env = mock_tmux.create_window.call_args.kwargs["extra_env"]
    assert extra_env["CAO_AGENT_DEPTH"] == "1"
