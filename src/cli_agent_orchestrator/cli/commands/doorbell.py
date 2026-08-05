"""Doorbell ingress commands — short external triggers to the king inbox."""

from __future__ import annotations

from pathlib import Path

import click
import requests

from cli_agent_orchestrator.doorbell.delivery import deliver_doorbell_trigger
from cli_agent_orchestrator.doorbell.routing import DoorbellRoutingError, resolve_king_terminal_id
from cli_agent_orchestrator.doorbell.validation import DoorbellValidationError


def _extract_detail(response: requests.Response, fallback: str) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except ValueError:
        pass
    return fallback


@click.group()
def doorbell():
    """Send short external triggers to the king inbox."""


@doorbell.command("send")
@click.option(
    "--to",
    "terminal_id",
    default=None,
    help=(
        "King terminal id (8-char hex). When omitted, read king_terminal_id "
        "from .swarm/state.json in the current working directory."
    ),
)
@click.option(
    "--trigger",
    required=True,
    help="Canonical trigger: [source:type:id] hint (max 200 characters).",
)
@click.option(
    "--state-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Override .swarm/state.json path when --to is omitted.",
)
def send_cmd(terminal_id, trigger, state_file):
    """Deliver a validated doorbell trigger to the king inbox.

    Uses POST /terminals/{id}/inbox/messages — the same path as send_message.
    Doorbell delivery never creates terminals or assigns workers.
    """
    try:
        receiver_id = resolve_king_terminal_id(
            explicit_to=terminal_id,
            state_path=state_file,
        )
    except DoorbellRoutingError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        result = deliver_doorbell_trigger(receiver_id=receiver_id, trigger=trigger)
    except DoorbellValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    except requests.exceptions.ConnectionError as exc:
        raise click.ClickException(f"Failed to connect to cao-server: {exc}") from exc
    except requests.HTTPError as exc:
        detail = str(exc)
        if exc.response is not None:
            detail = _extract_detail(exc.response, detail)
        raise click.ClickException(
            f"Failed to deliver doorbell to terminal {receiver_id}: {detail}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise click.ClickException(f"Failed to connect to cao-server: {exc}") from exc

    click.echo(
        f"Doorbell delivered to {receiver_id} "
        f"(message_id={result.get('message_id')}, trigger={result.get('delivered_message')!r})"
    )
