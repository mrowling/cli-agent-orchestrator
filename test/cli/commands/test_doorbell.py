"""Tests for doorbell delivery and CLI."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.doorbell import doorbell
from cli_agent_orchestrator.doorbell.delivery import DOORBELL_SENDER_ID, deliver_doorbell_trigger
from cli_agent_orchestrator.doorbell.validation import DOORBELL_MAX_TRIGGER_CHARS


@pytest.fixture
def runner():
    return CliRunner()


TRIGGER = "[github:pr_checks:org/repo#42] failing"


class TestDeliverDoorbellTrigger:
    @patch("cli_agent_orchestrator.doorbell.delivery.requests.post")
    def test_delivers_validated_trigger_via_inbox_only(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": True, "message_id": 7},
        )

        result = deliver_doorbell_trigger(receiver_id="abcd1234", trigger=TRIGGER)

        mock_post.assert_called_once()
        call = mock_post.call_args
        assert call.args[0].endswith("/terminals/abcd1234/inbox/messages")
        assert call.kwargs["params"]["sender_id"] == DOORBELL_SENDER_ID
        assert call.kwargs["params"]["message"] == TRIGGER
        assert result["delivered_message"] == TRIGGER

    @patch("cli_agent_orchestrator.doorbell.delivery.requests.post")
    def test_rejects_overlength_before_http(self, mock_post):
        long_hint = "x" * (DOORBELL_MAX_TRIGGER_CHARS - 10)
        trigger = f"[github:pr_checks:org/repo#42] {long_hint}"
        with pytest.raises(Exception, match="never truncated"):
            deliver_doorbell_trigger(receiver_id="abcd1234", trigger=trigger)
        mock_post.assert_not_called()

    @patch("cli_agent_orchestrator.doorbell.delivery.requests.post")
    def test_rejects_newline_before_http(self, mock_post):
        with pytest.raises(Exception, match="single line"):
            deliver_doorbell_trigger(
                receiver_id="abcd1234",
                trigger=f"{TRIGGER}\nextra",
            )
        mock_post.assert_not_called()


class TestDoorbellCli:
    @patch("cli_agent_orchestrator.cli.commands.doorbell.deliver_doorbell_trigger")
    def test_send_explicit_to(self, mock_deliver, runner):
        mock_deliver.return_value = {
            "message_id": 1,
            "delivered_message": TRIGGER,
        }

        result = runner.invoke(
            doorbell,
            ["send", "--to", "abcd1234", "--trigger", TRIGGER],
        )

        assert result.exit_code == 0
        mock_deliver.assert_called_once_with(receiver_id="abcd1234", trigger=TRIGGER)
        assert "abcd1234" in result.output

    @patch("cli_agent_orchestrator.cli.commands.doorbell.deliver_doorbell_trigger")
    @patch("cli_agent_orchestrator.cli.commands.doorbell.resolve_king_terminal_id")
    def test_send_routes_via_state_when_to_omitted(self, mock_resolve, mock_deliver, runner):
        mock_resolve.return_value = "feedbeef"
        mock_deliver.return_value = {
            "message_id": 2,
            "delivered_message": TRIGGER,
        }

        result = runner.invoke(doorbell, ["send", "--trigger", TRIGGER])

        assert result.exit_code == 0
        mock_resolve.assert_called_once()
        mock_deliver.assert_called_once_with(receiver_id="feedbeef", trigger=TRIGGER)

    @patch("cli_agent_orchestrator.doorbell.delivery.requests.post")
    def test_send_validation_error_before_delivery(self, mock_post, runner):
        result = runner.invoke(
            doorbell,
            ["send", "--to", "abcd1234", "--trigger", "not-a-trigger"],
        )

        assert result.exit_code != 0
        assert "must match" in result.output
        mock_post.assert_not_called()

    @patch("cli_agent_orchestrator.cli.commands.doorbell.deliver_doorbell_trigger")
    @patch("cli_agent_orchestrator.cli.commands.doorbell.resolve_king_terminal_id")
    def test_send_state_routing_error(self, mock_resolve, mock_deliver, runner):
        from cli_agent_orchestrator.doorbell.routing import DoorbellRoutingError

        mock_resolve.side_effect = DoorbellRoutingError("missing king_terminal_id")

        result = runner.invoke(doorbell, ["send", "--trigger", TRIGGER])

        assert result.exit_code != 0
        assert "missing king_terminal_id" in result.output
        mock_deliver.assert_not_called()

    @patch("cli_agent_orchestrator.cli.commands.doorbell.requests.post")
    @patch("cli_agent_orchestrator.cli.commands.doorbell.deliver_doorbell_trigger")
    def test_send_never_calls_spawn_or_assign_routes(self, mock_deliver, mock_post, runner):
        """Doorbell CLI must not hit terminal create/assign/handoff endpoints."""
        mock_deliver.return_value = {
            "message_id": 3,
            "delivered_message": TRIGGER,
        }

        result = runner.invoke(
            doorbell,
            ["send", "--to", "abcd1234", "--trigger", TRIGGER],
        )

        assert result.exit_code == 0
        mock_post.assert_not_called()
        for call in mock_deliver.call_args_list:
            assert "assign" not in str(call)
            assert "handoff" not in str(call)

    @patch("cli_agent_orchestrator.doorbell.delivery.requests.post")
    def test_delivery_module_only_posts_inbox(self, mock_post, runner):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": True, "message_id": 9},
        )

        deliver_doorbell_trigger(receiver_id="abcd1234", trigger=TRIGGER)

        assert mock_post.call_count == 1
        url = mock_post.call_args.args[0]
        assert url.endswith("/inbox/messages")
        assert "/terminals/" in url
        assert "assign" not in url
        assert "handoff" not in url
        assert "run-step" not in url

    @patch("cli_agent_orchestrator.cli.commands.doorbell.deliver_doorbell_trigger")
    def test_send_reports_api_failure(self, mock_deliver, runner):
        response = MagicMock(status_code=404)
        response.json.return_value = {"detail": "Terminal 'abcd1234' not found"}
        mock_deliver.side_effect = requests.HTTPError(response=response)

        result = runner.invoke(
            doorbell,
            ["send", "--to", "abcd1234", "--trigger", TRIGGER],
        )

        assert result.exit_code != 0
        assert "not found" in result.output
