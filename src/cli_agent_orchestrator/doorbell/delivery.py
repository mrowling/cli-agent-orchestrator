"""Deliver validated doorbell triggers through the inbox API."""

from __future__ import annotations

from typing import Any, Dict

import requests

from cli_agent_orchestrator.constants import API_BASE_URL, MCP_REQUEST_TIMEOUT
from cli_agent_orchestrator.doorbell.validation import validate_doorbell_trigger
from cli_agent_orchestrator.security.auth import get_local_bearer

# Fixed sender for external ingress — not a live CAO terminal row.
DOORBELL_SENDER_ID = "doorbell"


def _auth_headers() -> Dict[str, str]:
    token = get_local_bearer()
    return {"Authorization": f"Bearer {token}"} if token else {}


def deliver_doorbell_trigger(*, receiver_id: str, trigger: str) -> Dict[str, Any]:
    """POST the validated trigger to the king inbox.

    The inbox ``message`` payload is exactly the validated trigger string —
    no wrapper prefix or suffix.

    Raises:
        requests.HTTPError: When the inbox API rejects the delivery.
        DoorbellValidationError: When ``trigger`` fails validation (pre-HTTP).
    """

    message = validate_doorbell_trigger(trigger)
    response = requests.post(
        f"{API_BASE_URL}/terminals/{receiver_id}/inbox/messages",
        params={
            "sender_id": DOORBELL_SENDER_ID,
            "message": message,
        },
        headers=_auth_headers() or None,
        timeout=MCP_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload: Dict[str, Any] = response.json()
    payload["delivered_message"] = message
    payload["receiver_id"] = receiver_id
    return payload
