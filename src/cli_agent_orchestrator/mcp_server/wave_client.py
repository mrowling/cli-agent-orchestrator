"""HTTP (or in-process) client for ADT-6 wave concurrency from the MCP boundary.

Production: cao-server owns the queue so ``delete_terminal`` (API or MCP) can
release slots and drain without bypass. MCP reaches it over HTTP.

Tests / single-process: set ``CAO_WAVE_INPROCESS=true`` to call the singleton
``wave_service`` directly (same process as the test harness).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, cast

import requests

from cli_agent_orchestrator.constants import API_BASE_URL, CAO_WAVE_WAIT_TIMEOUT_MAX
from cli_agent_orchestrator.mcp_server.utils import _auth_headers
from cli_agent_orchestrator.services.settings_service import get_server_settings

logger = logging.getLogger(__name__)


def _timeout() -> float:
    return float(get_server_settings()["mcp_request_timeout"])


def _inprocess() -> bool:
    return os.environ.get("CAO_WAVE_INPROCESS", "").lower() in ("1", "true", "yes")


def _clamp_wait_timeout(timeout: Optional[float]) -> float:
    """Bound wave wait timeouts; never unbounded."""
    if timeout is None:
        return float(CAO_WAVE_WAIT_TIMEOUT_MAX)
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return float(CAO_WAVE_WAIT_TIMEOUT_MAX)
    if value <= 0:
        return float(CAO_WAVE_WAIT_TIMEOUT_MAX)
    return min(value, float(CAO_WAVE_WAIT_TIMEOUT_MAX))


def try_admit(
    supervisor_id: str,
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Admit or queue a wave request. Returns a JSON-shaped decision dict."""
    if _inprocess():
        from cli_agent_orchestrator.services.wave_concurrency import wave_service

        result = wave_service.try_admit(supervisor_id, kind, payload)
        return {
            "status": result.status.value,
            "reservation_id": result.reservation_id,
            "queue_id": result.queue_id,
            "position": result.position,
            "terminal_id": result.terminal_id,
            "message": result.message,
        }

    response = requests.post(
        f"{API_BASE_URL}/wave/admit",
        json={
            "supervisor_id": supervisor_id,
            "kind": kind,
            "payload": payload or {},
        },
        headers=_auth_headers() or None,
        timeout=_timeout(),
    )
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())


def bind_terminal(reservation_id: str, terminal_id: str) -> None:
    if _inprocess():
        from cli_agent_orchestrator.services.wave_concurrency import wave_service

        wave_service.bind_terminal(reservation_id, terminal_id)
        return

    response = requests.post(
        f"{API_BASE_URL}/wave/bind",
        json={"reservation_id": reservation_id, "terminal_id": terminal_id},
        headers=_auth_headers() or None,
        timeout=_timeout(),
    )
    response.raise_for_status()


def release(
    *,
    reservation_id: Optional[str] = None,
    terminal_id: Optional[str] = None,
) -> None:
    if _inprocess():
        from cli_agent_orchestrator.services.wave_concurrency import wave_service

        wave_service.release(reservation_id=reservation_id, terminal_id=terminal_id)
        return

    response = requests.post(
        f"{API_BASE_URL}/wave/release",
        json={"reservation_id": reservation_id, "terminal_id": terminal_id},
        headers=_auth_headers() or None,
        timeout=_timeout(),
    )
    response.raise_for_status()


def cancel_request(queue_id: str, *, reason: str = "cancelled") -> None:
    """Abandon a queued wave request so it cannot orphan-admit later."""
    if _inprocess():
        from cli_agent_orchestrator.services.wave_concurrency import wave_service

        wave_service.cancel_request(queue_id, reason=reason)
        return

    response = requests.post(
        f"{API_BASE_URL}/wave/cancel",
        json={"queue_id": queue_id, "reason": reason},
        headers=_auth_headers() or None,
        timeout=_timeout(),
    )
    response.raise_for_status()


def requeue_after_global_cap(
    reservation_id: str,
    *,
    kind: str,
    payload: Dict[str, Any],
    queue_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Release an admitted slot and re-queue at FIFO front after D7 rejection."""
    if _inprocess():
        from cli_agent_orchestrator.services.wave_concurrency import wave_service

        qid = wave_service.release_reservation_and_requeue_front(
            reservation_id, kind=kind, payload=payload, queue_id=queue_id
        )
        return {"queue_id": qid, "status": "queued"}

    response = requests.post(
        f"{API_BASE_URL}/wave/requeue-front",
        json={
            "reservation_id": reservation_id,
            "kind": kind,
            "payload": payload,
            "queue_id": queue_id,
        },
        headers=_auth_headers() or None,
        timeout=_timeout(),
    )
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())


def wait_for_admission(queue_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Block until a queued request is admitted (event-driven; no poll loop)."""
    clamped = _clamp_wait_timeout(timeout)
    if _inprocess():
        from cli_agent_orchestrator.services.wave_concurrency import wave_service

        result = wave_service.wait_for_admission(queue_id, timeout=clamped)
        return {
            "status": result.status.value,
            "reservation_id": result.reservation_id,
            "queue_id": result.queue_id,
            "terminal_id": result.terminal_id,
            "message": result.message,
        }

    # Client HTTP timeout must outlive the server-side wait.
    http_timeout = clamped + 30.0
    response = requests.get(
        f"{API_BASE_URL}/wave/wait/{queue_id}",
        params={"timeout": clamped},
        headers=_auth_headers() or None,
        timeout=http_timeout,
    )
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())
