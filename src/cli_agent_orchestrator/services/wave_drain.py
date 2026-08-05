"""Async assign drain helpers for ADT-6 wave concurrency.

Separated from ``wave_concurrency`` so the lock-safe queue service stays free of
terminal_service / inbox imports (unit-testable in isolation). The API lifespan
registers the event loop; ``schedule_assign_drains`` is safe to call from the
sync ``delete_terminal`` path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from cli_agent_orchestrator.clients.database import create_inbox_message
from cli_agent_orchestrator.models.inbox import OrchestrationType
from cli_agent_orchestrator.services.terminal_service import (
    TerminalCapacityError,
    WaveReservationBindError,
)
from cli_agent_orchestrator.services.wave_concurrency import (
    WaveKind,
    WaveRequestStatus,
    wave_service,
)

logger = logging.getLogger(__name__)

_loop: Optional[asyncio.AbstractEventLoop] = None
_drain_tasks: Set[asyncio.Task] = set()


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the cao-server event loop for thread-safe drain scheduling."""
    global _loop
    _loop = loop


def schedule_assign_drains(queue_ids: List[str]) -> None:
    """Schedule async create for drained assign queue_ids (thread-safe)."""
    if not queue_ids:
        return
    loop = _loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "Wave assign drain deferred: no event loop for queue_ids=%s",
                queue_ids,
            )
            wave_service.repend_assign_drains(queue_ids)
            return

    for queue_id in queue_ids:
        try:
            fut = asyncio.run_coroutine_threadsafe(_drain_assign(queue_id), loop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to schedule wave assign drain for %s: %s", queue_id, exc)
            continue

        def _done(f: Any, *, qid: str = queue_id) -> None:
            try:
                f.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Wave assign drain task failed for %s: %s", qid, exc)

        fut.add_done_callback(_done)


async def _drain_assign(queue_id: str) -> None:
    """Create the terminal for a drained assign request; notify the supervisor."""
    req = wave_service.get_request(queue_id)
    if req is None or req.kind != WaveKind.ASSIGN:
        return
    if req.status not in (WaveRequestStatus.ADMITTED, WaveRequestStatus.QUEUED):
        # STARTED already, or released
        if req.status == WaveRequestStatus.STARTED:
            return
    payload: Dict[str, Any] = dict(req.payload or {})
    reservation_id = req.reservation_id
    supervisor_id = req.supervisor_id

    from cli_agent_orchestrator.services import terminal_service

    try:
        orch_raw = payload.get("initial_message_orchestration_type")
        orch_type = None
        if orch_raw:
            try:
                orch_type = OrchestrationType(orch_raw)
            except ValueError:
                orch_type = OrchestrationType.ASSIGN

        terminal = await terminal_service.create_terminal(
            provider=payload["provider"],
            agent_profile=payload["agent_profile"],
            session_name=payload.get("session_name"),
            new_session=False,
            working_directory=payload.get("working_directory"),
            allowed_tools=payload.get("allowed_tools"),
            caller_id=supervisor_id,
            defer_init=True,
            initial_message=payload.get("message"),
            initial_message_orchestration_type=orch_type or OrchestrationType.ASSIGN,
            model=payload.get("model"),
            env_vars=payload.get("env_vars"),
            workspace=payload.get("workspace"),
            wave_reservation_id=reservation_id,
        )
    except TerminalCapacityError as exc:
        logger.info(
            "Wave assign drain hit global cap for queue_id=%s; re-queueing at front: %s",
            queue_id,
            exc,
        )
        if reservation_id:
            wave_service.release_reservation_and_requeue_front(
                reservation_id,
                kind=WaveKind.ASSIGN,
                payload={**payload, "supervisor_id": supervisor_id},
                queue_id=queue_id,
            )
        else:
            wave_service.requeue_front(
                supervisor_id,
                WaveKind.ASSIGN,
                payload,
                queue_id=queue_id,
                blocked_on_global_cap=True,
            )
        return
    except WaveReservationBindError as exc:
        logger.warning(
            "Wave assign drain bind failed for queue_id=%s (no side effects): %s",
            queue_id,
            exc,
        )
        # Reservation already expired/cancelled — do not mark_assign_started.
        # Slot was freed by reap; request should already be CANCELLED.
        try:
            create_inbox_message(
                sender_id=supervisor_id,
                receiver_id=supervisor_id,
                message=(
                    f"[CAO wave] Queued assign {queue_id} aborted: wave reservation "
                    f"expired or cancelled before create. {exc}"
                ),
            )
        except Exception:
            pass
        return
    except Exception as exc:
        logger.error("Wave assign drain create failed for %s: %s", queue_id, exc)
        if reservation_id:
            # Free the slot so siblings can proceed; leave request inspectable.
            wave_service.release(reservation_id=reservation_id)
        try:
            create_inbox_message(
                sender_id=supervisor_id,
                receiver_id=supervisor_id,
                message=(
                    f"[CAO wave] Queued assign {queue_id} failed to start: {exc}. "
                    f"Wave slot released."
                ),
            )
        except Exception:
            pass
        return

    if not wave_service.mark_assign_started(queue_id, terminal.id):
        logger.warning(
            "Wave assign drain mark_assign_started refused for queue_id=%s "
            "terminal=%s; deleting orphan terminal and releasing reservation",
            queue_id,
            terminal.id,
        )
        try:
            await asyncio.to_thread(terminal_service.delete_terminal, terminal.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Wave assign drain failed to delete orphan terminal %s: %s",
                terminal.id,
                exc,
            )
            if reservation_id:
                wave_service.release(reservation_id=reservation_id)
        try:
            create_inbox_message(
                sender_id=supervisor_id,
                receiver_id=supervisor_id,
                message=(
                    f"[CAO wave] Queued assign {queue_id} aborted after create: "
                    f"wave reservation expired or cancelled before start. "
                    f"Terminal {terminal.id} was removed."
                ),
            )
        except Exception:
            pass
        return

    ws_bits = []
    if getattr(terminal, "workspace_backend", None):
        ws_bits.append(f"workspace={terminal.workspace_backend}")
    if getattr(terminal, "workspace_path", None):
        ws_bits.append(f"path={terminal.workspace_path}")
    if getattr(terminal, "workspace_branch", None):
        ws_bits.append(f"branch={terminal.workspace_branch}")
    ws_note = (" " + " ".join(ws_bits)) if ws_bits else ""
    notify = (
        f"[CAO wave] Queued assign {queue_id} started as terminal {terminal.id} "
        f"(profile={payload.get('agent_profile')}).{ws_note} "
        f"Call delete_terminal('{terminal.id}') when finished to free the wave slot."
    )
    try:
        create_inbox_message(
            sender_id=terminal.id,
            receiver_id=supervisor_id,
            message=notify,
        )
        # Best-effort immediate delivery if supervisor is idle.
        try:
            inbox_service = __import__(
                "cli_agent_orchestrator.services.inbox_service", fromlist=["inbox_service"]
            ).inbox_service
            inbox_service.deliver_pending(supervisor_id)
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Wave assign started (%s) but inbox notify to %s failed: %s",
            terminal.id,
            supervisor_id,
            exc,
        )
