"""Per-supervisor wave concurrency queue for assign/handoff (ADT-6).

Limits how many assign/handoff children one supervisor may hold in-flight at
once (``CAO_MAX_WAVE_IN_FLIGHT``, default 3). Excess requests are admitted as
``queued`` in a process-local side table (not ``TerminalStatus`` — no terminal
row exists until a slot is granted and create succeeds).

Design notes:
- Slots are keyed by supervisor (caller) terminal id. Only assign/handoff
  admission is tracked — unrelated session terminals are not counted.
- Conservative: all assign/handoff children share the budget (no fragile
  profile-name matching for "implementer" roles).
- Race-safe via ``threading.RLock`` (delete runs in worker threads; admit/wait
  from the async API loop).
- Event-driven drain: ``threading.Event`` wakes queued handoff waiters; assign
  drain is scheduled via a registered async callback (no sleep/poll loops).
- ``CAO_MAX_ACTIVE_TERMINALS`` remains the hard ceiling: a global-cap rejection
  re-queues the request at the front and retries on a later terminal deletion.
- Handoff ``try_admit`` registers **live intent** + an expiring lease atomically
  with the queue entry so a sibling release/delete between enqueue and
  ``wait_for_admission`` cannot cancel the FIFO head. Only explicit cancel or
  lease expiry abandons ownership. Requeue-after-429 preserves that intent.
- Unbound in-flight reservations carry short monotonic leases so MCP death
  after admit but before create/bind cannot steal capacity forever. Bound
  live terminals never expire; deletion owns their slot cleanup.
- Expired unbound reservations / abandoned queued handoffs are reaped on every
  admit/release/delete/status/wait/drain entry (no background busy poll), then
  event-driven drain proceeds.
- ``release`` of an unknown id is a no-op (does not global-drain). Capacity
  recovery after any terminal deletion only re-drains heads blocked on the
  global cap.
- On cao-server startup / first queue use, live terminals with ``caller_id``
  are reconciled into conservative in-flight reservations under the service
  lock. Reconcile is fail-closed: DB failure leaves ``_reconciled=False`` and
  rejects admission rather than admitting under an unknown count. Concurrent
  first admits serialize behind reconcile. Queued-but-not-started requests are
  in-memory and intentionally lost on restart.
- Handoff logical completion releases its wave slot even when a diagnostic
  terminal is preserved (global terminal cap still counts that terminal).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Sequence

from cli_agent_orchestrator.constants import (
    CAO_MAX_WAVE_IN_FLIGHT,
    CAO_WAVE_HANDOFF_QUEUE_LEASE_S,
    CAO_WAVE_UNBOUND_LEASE_S,
)

logger = logging.getLogger(__name__)

# Optional async drain hook registered by the API layer so assign auto-start
# can call ``create_terminal`` on the event loop without this module importing
# terminal_service (keeps the service unit-testable in isolation).
AssignDrainCallback = Callable[[str, Dict[str, Any]], Awaitable[Optional[str]]]
# queue_id, payload -> terminal_id or None on deferrable failure (global cap)

ClockFn = Callable[[], float]


class WaveReconcileError(RuntimeError):
    """Raised when wave admission cannot proceed because reconcile failed.

    Callers must treat this as actionable deferral — do not admit under an
    unknown in-flight count.
    """


class WaveKind(str, Enum):
    ASSIGN = "assign"
    HANDOFF = "handoff"


class WaveRequestStatus(str, Enum):
    """Side-table status for wave requests (not TerminalStatus)."""

    QUEUED = "queued"
    ADMITTED = "admitted"
    STARTED = "started"
    RELEASED = "released"
    CANCELLED = "cancelled"


@dataclass
class InFlightSlot:
    reservation_id: str
    supervisor_id: str
    kind: WaveKind
    terminal_id: Optional[str] = None
    # Monotonic deadline for unbound reservations; None once bound (never expires).
    lease_expires_at: Optional[float] = None
    # Optional back-pointer to the queue entry that owns this reservation.
    queue_id: Optional[str] = None


@dataclass
class QueuedWaveRequest:
    queue_id: str
    supervisor_id: str
    kind: WaveKind
    payload: Dict[str, Any] = field(default_factory=dict)
    status: WaveRequestStatus = WaveRequestStatus.QUEUED
    # Set when a queued handoff is promoted; waiter observes via Event.
    reservation_id: Optional[str] = None
    admitted_event: threading.Event = field(default_factory=threading.Event)
    # Set when a queued assign finishes create; waiters / tests can observe.
    started_event: threading.Event = field(default_factory=threading.Event)
    terminal_id: Optional[str] = None
    # Global-cap: parked at front until a later deletion frees session capacity.
    blocked_on_global_cap: bool = False
    # Live HTTP waiters registered via wait_for_admission (lease refresh / orphan gate).
    waiter_count: int = 0
    # Atomic ownership from try_admit / requeue: survives until cancel or lease expiry.
    # Drain must not cancel a handoff head that still has live intent.
    live_intent: bool = False
    # Monotonic deadline for queued handoff ownership (ignored while waiter_count > 0).
    lease_expires_at: Optional[float] = None


@dataclass
class WaveAdmitResult:
    status: WaveRequestStatus
    reservation_id: Optional[str] = None
    queue_id: Optional[str] = None
    position: Optional[int] = None
    terminal_id: Optional[str] = None
    message: str = ""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class WaveConcurrencyService:
    """Process-local FIFO wave queue + in-flight slot tracker per supervisor."""

    def __init__(
        self,
        max_in_flight: Optional[int] = None,
        *,
        clock: Optional[ClockFn] = None,
        unbound_lease_s: Optional[float] = None,
        handoff_queue_lease_s: Optional[float] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._max_in_flight = max_in_flight if max_in_flight is not None else CAO_MAX_WAVE_IN_FLIGHT
        self._clock: ClockFn = clock or time.monotonic
        self._unbound_lease_s = (
            float(unbound_lease_s)
            if unbound_lease_s is not None
            else float(CAO_WAVE_UNBOUND_LEASE_S)
        )
        self._handoff_queue_lease_s = (
            float(handoff_queue_lease_s)
            if handoff_queue_lease_s is not None
            else float(CAO_WAVE_HANDOFF_QUEUE_LEASE_S)
        )
        self._in_flight: Dict[str, Dict[str, InFlightSlot]] = {}
        # supervisor_id -> reservation_id -> slot
        self._by_terminal: Dict[str, str] = {}  # terminal_id -> reservation_id
        self._queues: Dict[str, Deque[QueuedWaveRequest]] = {}
        self._requests: Dict[str, QueuedWaveRequest] = {}  # queue_id -> request
        self._reservations: Dict[str, InFlightSlot] = {}  # reservation_id -> slot
        self._assign_drain_callback: Optional[AssignDrainCallback] = None
        self._pending_assign_drains: List[str] = []  # queue_ids awaiting async create
        self._reconciled: bool = False

    # ------------------------------------------------------------------ config
    @property
    def max_in_flight(self) -> int:
        with self._lock:
            return self._max_in_flight

    def set_max_in_flight(self, value: int) -> None:
        with self._lock:
            self._max_in_flight = max(1, int(value))

    def set_clock(self, clock: ClockFn) -> None:
        """Inject a monotonic clock (tests)."""
        with self._lock:
            self._clock = clock

    def set_lease_ttls(
        self,
        *,
        unbound_lease_s: Optional[float] = None,
        handoff_queue_lease_s: Optional[float] = None,
    ) -> None:
        """Override lease TTLs (tests / ops)."""
        with self._lock:
            if unbound_lease_s is not None:
                self._unbound_lease_s = max(0.001, float(unbound_lease_s))
            if handoff_queue_lease_s is not None:
                self._handoff_queue_lease_s = max(0.001, float(handoff_queue_lease_s))

    def set_assign_drain_callback(self, callback: Optional[AssignDrainCallback]) -> None:
        """Register async callback invoked to start a drained assign request."""
        with self._lock:
            self._assign_drain_callback = callback

    def reset(self) -> None:
        """Clear all state (tests)."""
        with self._lock:
            self._in_flight.clear()
            self._by_terminal.clear()
            self._queues.clear()
            self._requests.clear()
            self._reservations.clear()
            self._pending_assign_drains.clear()
            self._reconciled = False

    def _now(self) -> float:
        return float(self._clock())

    def _unbound_deadline(self) -> float:
        return self._now() + self._unbound_lease_s

    def _handoff_queue_deadline(self) -> float:
        return self._now() + self._handoff_queue_lease_s

    # -------------------------------------------------------------- reconcile
    def ensure_reconciled(self) -> int:
        """Lazily rebuild in-flight slots from live DB terminals (once per process).

        Fail-closed and serialized: under the service lock, list DB terminals,
        restore conservative caller_id child slots, then mark reconciled only on
        success. On DB failure leave ``_reconciled=False`` and raise
        ``WaveReconcileError`` so callers do not admit under an unknown count.
        """
        with self._lock:
            return self._ensure_reconciled_locked()

    def _ensure_reconciled_locked(self) -> int:
        """Caller must hold ``self._lock``."""
        if self._reconciled:
            return 0
        try:
            from cli_agent_orchestrator.clients.database import list_terminals_with_caller

            children = list_terminals_with_caller()
        except Exception as exc:  # noqa: BLE001 — surface as actionable deferral
            logger.warning("Wave reconcile failed (DB unavailable): %s", exc)
            # Leave _reconciled=False so a later call can retry.
            raise WaveReconcileError(
                "Wave admission deferred: cannot reconcile live children from DB " f"({exc})"
            ) from exc
        created = self._apply_reconcile_locked(children)
        self._reconciled = True
        return created

    def reconcile_in_flight_from_terminals(self, terminals: Sequence[Dict[str, Any]]) -> int:
        """Rebuild conservative assign-child reservations from terminal metadata.

        Any live terminal with a non-null ``caller_id`` counts as one in-flight
        child under that supervisor. Already-bound terminals are left alone.
        Returns the number of new reservations created.

        Queued-but-not-started requests are process-local and are intentionally
        not restored after a server restart. Marks the service reconciled on
        success (caller already obtained the terminal list).
        """
        with self._lock:
            created = self._apply_reconcile_locked(terminals)
            self._reconciled = True
            return created

    def _apply_reconcile_locked(self, terminals: Sequence[Dict[str, Any]]) -> int:
        """Restore slots from terminal rows. Caller holds lock. Does not flip flag."""
        created = 0
        for row in terminals:
            terminal_id = str(row.get("id") or "")
            supervisor_id = row.get("caller_id")
            if not terminal_id or not supervisor_id:
                continue
            supervisor_id = str(supervisor_id)
            if terminal_id in self._by_terminal:
                continue
            reservation_id = _new_id("wres")
            slot = InFlightSlot(
                reservation_id=reservation_id,
                supervisor_id=supervisor_id,
                kind=WaveKind.ASSIGN,
                terminal_id=terminal_id,
                lease_expires_at=None,  # bound — never expires
            )
            self._in_flight.setdefault(supervisor_id, {})[reservation_id] = slot
            self._reservations[reservation_id] = slot
            self._by_terminal[terminal_id] = reservation_id
            created += 1
            logger.info(
                "Wave reconcile: restored in-flight slot supervisor=%s "
                "terminal=%s reservation=%s",
                supervisor_id,
                terminal_id,
                reservation_id,
            )
        if created:
            logger.info(
                "Wave reconcile restored %s in-flight reservation(s) from live terminals",
                created,
            )
        return created

    @property
    def is_reconciled(self) -> bool:
        with self._lock:
            return self._reconciled

    # ------------------------------------------------------------------- reap
    def _reap_expired_locked(self) -> List[str]:
        """Cancel abandoned queued handoffs and free expired unbound reservations.

        Caller holds lock. Returns assign queue_ids that need async create after
        any resulting drain. Bound terminals are never expired.
        """
        now = self._now()
        pending: List[str] = []

        # 1) Abandoned queued handoffs (no live HTTP waiter + lease expired).
        abandoned_qids: List[str] = []
        for req in list(self._requests.values()):
            if req.status != WaveRequestStatus.QUEUED:
                continue
            if req.kind != WaveKind.HANDOFF:
                continue
            if not req.live_intent:
                # Should not happen for try_admit handoffs; treat as abandonable.
                abandoned_qids.append(req.queue_id)
                continue
            if req.waiter_count > 0:
                continue
            if req.lease_expires_at is None or req.lease_expires_at > now:
                continue
            abandoned_qids.append(req.queue_id)

        for qid in abandoned_qids:
            pending.extend(self._cancel_request_locked(qid, reason="handoff queue lease expired"))

        # 2) Expired unbound in-flight reservations (MCP died before bind).
        expired_rids: List[str] = []
        for rid, slot in list(self._reservations.items()):
            if slot.terminal_id is not None:
                continue  # bound — never expires
            if slot.lease_expires_at is None or slot.lease_expires_at > now:
                continue
            expired_rids.append(rid)

        for rid in expired_rids:
            expired_slot = self._reservations.get(rid)
            if expired_slot is None:
                continue
            # Cancel associated admitted-unbound request if present.
            expired_qid = expired_slot.queue_id
            if expired_qid:
                expired_req = self._requests.get(expired_qid)
                if expired_req is not None and expired_req.status == WaveRequestStatus.ADMITTED:
                    expired_req.status = WaveRequestStatus.CANCELLED
                    expired_req.live_intent = False
                    expired_req.lease_expires_at = None
                    expired_req.blocked_on_global_cap = False
                    expired_req.admitted_event.set()
                    logger.info(
                        "Wave unbound lease expired: cancelled admitted request "
                        "queue_id=%s reservation=%s",
                        expired_qid,
                        rid,
                    )
            logger.info(
                "Wave unbound lease expired: releasing reservation=%s " "supervisor=%s kind=%s",
                rid,
                expired_slot.supervisor_id,
                expired_slot.kind.value,
            )
            pending.extend(self._release_locked(reservation_id=rid))

        # Deduplicate while preserving order.
        seen = set()
        ordered: List[str] = []
        for qid in pending:
            if qid not in seen:
                seen.add(qid)
                ordered.append(qid)
        return ordered

    def reap_expired(self) -> List[str]:
        """Public reap entry (tests / ops)."""
        with self._lock:
            return self._reap_expired_locked()

    # ------------------------------------------------------------------- admit
    def try_admit(
        self,
        supervisor_id: str,
        kind: WaveKind | str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> WaveAdmitResult:
        """Admit immediately or enqueue FIFO for ``supervisor_id``.

        Returns ``ADMITTED`` with a ``reservation_id``, or ``QUEUED`` with a
        stable ``queue_id``. Payload is preserved exactly for later drain.

        Handoff queue entries register live intent + lease atomically under the
        same lock so a concurrent release cannot cancel them before wait begins.
        Unbound admissions receive a short lease until ``bind_terminal``.
        """
        if not supervisor_id:
            raise ValueError("supervisor_id is required for wave admission")
        wave_kind = WaveKind(kind) if not isinstance(kind, WaveKind) else kind
        payload = dict(payload or {})

        with self._lock:
            pending = self._reap_expired_locked()
            if pending:
                self._pending_assign_drains.extend(
                    qid for qid in pending if qid not in self._pending_assign_drains
                )
            self._ensure_reconciled_locked()

            # Preserve FIFO: promote any live queue heads before a newcomer can
            # steal a free wave slot (e.g. requeue-after-429 left a handoff at
            # front with in_flight=0).
            drained = self._drain_supervisor_locked(supervisor_id)
            for qid in drained:
                if qid not in self._pending_assign_drains:
                    self._pending_assign_drains.append(qid)

            cap = self._max_in_flight
            in_flight = self._in_flight.setdefault(supervisor_id, {})
            if len(in_flight) < cap:
                reservation_id = _new_id("wres")
                slot = InFlightSlot(
                    reservation_id=reservation_id,
                    supervisor_id=supervisor_id,
                    kind=wave_kind,
                    lease_expires_at=self._unbound_deadline(),
                )
                in_flight[reservation_id] = slot
                self._reservations[reservation_id] = slot
                return WaveAdmitResult(
                    status=WaveRequestStatus.ADMITTED,
                    reservation_id=reservation_id,
                    message=(
                        f"Wave slot admitted ({len(in_flight)}/{cap} in flight "
                        f"for supervisor {supervisor_id})"
                    ),
                )

            queue_id = _new_id("wque")
            req = QueuedWaveRequest(
                queue_id=queue_id,
                supervisor_id=supervisor_id,
                kind=wave_kind,
                payload=payload,
                status=WaveRequestStatus.QUEUED,
            )
            if wave_kind == WaveKind.HANDOFF:
                # Atomic live intent: drain must leave this head in place until
                # wait begins, cancel, or lease expiry — never cancel merely
                # because HTTP wait has not started yet.
                req.live_intent = True
                req.lease_expires_at = self._handoff_queue_deadline()
            q = self._queues.setdefault(supervisor_id, deque())
            q.append(req)
            self._requests[queue_id] = req
            position = len(q)
            logger.info(
                "Wave request queued: supervisor=%s kind=%s queue_id=%s position=%s "
                "(in_flight=%s/%s live_intent=%s)",
                supervisor_id,
                wave_kind.value,
                queue_id,
                position,
                len(in_flight),
                cap,
                req.live_intent,
            )
            return WaveAdmitResult(
                status=WaveRequestStatus.QUEUED,
                queue_id=queue_id,
                position=position,
                message=(
                    f"Wave concurrency limit reached ({cap} in flight). "
                    f"Request queued at position {position} (queue_id={queue_id}). "
                    f"Assign slots free on delete_terminal; handoff slots free on "
                    f"handoff completion."
                ),
            )

    def bind_terminal(self, reservation_id: str, terminal_id: str) -> bool:
        """Attach a created terminal to an admitted reservation.

        Clears the unbound lease — bound live terminals never expire; deletion
        owns slot cleanup thereafter.
        """
        with self._lock:
            self._reap_expired_locked()
            slot = self._reservations.get(reservation_id)
            if slot is None:
                return False
            if slot.terminal_id and slot.terminal_id != terminal_id:
                logger.warning(
                    "Wave bind refused: reservation %s already bound to %s",
                    reservation_id,
                    slot.terminal_id,
                )
                return False
            slot.terminal_id = terminal_id
            slot.lease_expires_at = None  # bound — never expires
            self._by_terminal[terminal_id] = reservation_id
            if slot.queue_id:
                req = self._requests.get(slot.queue_id)
                if req is not None:
                    req.terminal_id = terminal_id
                    req.lease_expires_at = None
            return True

    # ----------------------------------------------------------------- release
    def release(
        self,
        *,
        reservation_id: Optional[str] = None,
        terminal_id: Optional[str] = None,
    ) -> List[str]:
        """Release an in-flight slot and drain that supervisor's FIFO queue.

        Unknown reservation/terminal is a no-op: it does **not** trigger a
        global drain (prevents unrelated handoff admission from stray release).
        Use ``on_terminal_deleted`` for capacity-recovery drains.
        """
        with self._lock:
            pending = self._reap_expired_locked()
            pending.extend(
                self._release_locked(reservation_id=reservation_id, terminal_id=terminal_id)
            )
            return self._dedupe(pending)

    def _release_locked(
        self,
        *,
        reservation_id: Optional[str] = None,
        terminal_id: Optional[str] = None,
    ) -> List[str]:
        """Caller holds lock. Does not reap (caller may already have)."""
        slot = None
        if reservation_id:
            slot = self._reservations.get(reservation_id)
        elif terminal_id:
            rid = self._by_terminal.get(terminal_id)
            if rid:
                slot = self._reservations.get(rid)
                reservation_id = rid

        if slot is None:
            logger.debug(
                "Wave release no-op for unknown reservation=%s terminal=%s " "(no global drain)",
                reservation_id,
                terminal_id,
            )
            return []

        supervisor_id = slot.supervisor_id
        self._reservations.pop(slot.reservation_id, None)
        bucket = self._in_flight.get(supervisor_id)
        if bucket is not None:
            bucket.pop(slot.reservation_id, None)
            if not bucket:
                self._in_flight.pop(supervisor_id, None)
        if slot.terminal_id:
            self._by_terminal.pop(slot.terminal_id, None)
        logger.info(
            "Wave slot released: supervisor=%s reservation=%s terminal=%s kind=%s",
            supervisor_id,
            slot.reservation_id,
            slot.terminal_id,
            slot.kind.value,
        )
        return self._drain_supervisor_locked(supervisor_id)

    def on_terminal_deleted(self, terminal_id: str) -> List[str]:
        """Release if this terminal held a wave slot; recover global-cap heads."""
        with self._lock:
            pending: List[str] = []
            pending.extend(self._reap_expired_locked())
            rid = self._by_terminal.get(terminal_id)
            if rid:
                pending.extend(self._release_locked(reservation_id=rid))
            # Any deletion may free CAO_MAX_ACTIVE_TERMINALS capacity — only
            # re-drain heads explicitly parked on the global cap.
            pending.extend(self._recover_global_cap_locked())
            return self._dedupe(pending)

    def _recover_global_cap_locked(self) -> List[str]:
        """Promote only queue heads blocked on the global terminal cap."""
        pending: List[str] = []
        for supervisor_id, q in list(self._queues.items()):
            if not q:
                continue
            head = q[0]
            if head.status != WaveRequestStatus.QUEUED or not head.blocked_on_global_cap:
                continue
            pending.extend(self._drain_supervisor_locked(supervisor_id))
        return pending

    def cancel_request(self, queue_id: str, *, reason: str = "cancelled") -> List[str]:
        """Abandon a queued (or newly admitted-without-waiter) request.

        Removes it from the FIFO so it cannot later transition to in-flight.
        Idempotent for unknown / already-terminal ids. Does not release an
        unrelated in-flight reservation that is already bound to a live terminal.
        Returns assign queue_ids that need async create if an orphan slot was freed.
        """
        with self._lock:
            pending = self._reap_expired_locked()
            pending.extend(self._cancel_request_locked(queue_id, reason=reason))
            return self._dedupe(pending)

    def _cancel_request_locked(self, queue_id: str, *, reason: str) -> List[str]:
        """Caller holds lock."""
        req = self._requests.get(queue_id)
        if req is None:
            return []
        if req.status in (
            WaveRequestStatus.STARTED,
            WaveRequestStatus.CANCELLED,
            WaveRequestStatus.RELEASED,
        ):
            return []

        # Authoritative reservation slot: refuse cancel/release when bound.
        if req.reservation_id:
            slot = self._reservations.get(req.reservation_id)
            if slot is not None and slot.terminal_id:
                logger.info(
                    "Wave cancel refused: queue_id=%s reservation=%s bound to terminal=%s",
                    queue_id,
                    req.reservation_id,
                    slot.terminal_id,
                )
                return []

        # If already admitted into an in-flight slot with no bound terminal,
        # free that slot too (abandon after admit before bind).
        orphan_reservation = None
        if req.status == WaveRequestStatus.ADMITTED and req.reservation_id:
            slot = self._reservations.get(req.reservation_id)
            if slot is not None and not slot.terminal_id:
                orphan_reservation = req.reservation_id

        q = self._queues.get(req.supervisor_id)
        if q is not None:
            try:
                q.remove(req)
            except ValueError:
                pass
            if not q:
                self._queues.pop(req.supervisor_id, None)

        req.status = WaveRequestStatus.CANCELLED
        req.blocked_on_global_cap = False
        req.live_intent = False
        req.lease_expires_at = None
        req.admitted_event.set()  # unblock any late waiter with CANCELLED
        logger.info(
            "Wave request cancelled: queue_id=%s supervisor=%s reason=%s",
            queue_id,
            req.supervisor_id,
            reason,
        )

        if orphan_reservation:
            return self._release_locked(reservation_id=orphan_reservation)
        return []

    def requeue_front(
        self,
        supervisor_id: str,
        kind: WaveKind | str,
        payload: Dict[str, Any],
        *,
        queue_id: Optional[str] = None,
        blocked_on_global_cap: bool = True,
    ) -> str:
        """Re-insert a request at the front after a deterministic global-cap reject.

        Preserves FIFO order relative to later arrivals. Handoff requeue
        preserves live intent + refreshes the queue lease so rematch wait can
        begin without a TOCTOU cancel window. Returns the queue_id.
        """
        wave_kind = WaveKind(kind) if not isinstance(kind, WaveKind) else kind
        with self._lock:
            self._reap_expired_locked()
            qid = queue_id or _new_id("wque")
            existing = self._requests.get(qid)
            if existing is not None:
                existing.status = WaveRequestStatus.QUEUED
                existing.payload = dict(payload)
                existing.blocked_on_global_cap = blocked_on_global_cap
                existing.reservation_id = None
                existing.terminal_id = None
                existing.admitted_event.clear()
                existing.started_event.clear()
                if wave_kind == WaveKind.HANDOFF:
                    existing.live_intent = True
                    existing.lease_expires_at = self._handoff_queue_deadline()
                else:
                    existing.live_intent = False
                    existing.lease_expires_at = None
                q = self._queues.setdefault(supervisor_id, deque())
                # Move to front if not already head.
                try:
                    q.remove(existing)
                except ValueError:
                    pass
                q.appendleft(existing)
                return qid

            req = QueuedWaveRequest(
                queue_id=qid,
                supervisor_id=supervisor_id,
                kind=wave_kind,
                payload=dict(payload),
                status=WaveRequestStatus.QUEUED,
                blocked_on_global_cap=blocked_on_global_cap,
            )
            if wave_kind == WaveKind.HANDOFF:
                req.live_intent = True
                req.lease_expires_at = self._handoff_queue_deadline()
            self._requests[qid] = req
            self._queues.setdefault(supervisor_id, deque()).appendleft(req)
            return qid

    def release_reservation_and_requeue_front(
        self,
        reservation_id: str,
        *,
        kind: WaveKind | str,
        payload: Dict[str, Any],
        queue_id: Optional[str] = None,
    ) -> str:
        """After admit + create failed on global cap: free slot, requeue at front."""
        with self._lock:
            self._reap_expired_locked()
            slot = self._reservations.pop(reservation_id, None)
            supervisor_id = None
            if slot is not None:
                supervisor_id = slot.supervisor_id
                bucket = self._in_flight.get(supervisor_id)
                if bucket is not None:
                    bucket.pop(reservation_id, None)
                    if not bucket:
                        self._in_flight.pop(supervisor_id, None)
                if slot.terminal_id:
                    self._by_terminal.pop(slot.terminal_id, None)
            if not supervisor_id:
                # Fall back to payload / fail soft
                supervisor_id = str(payload.get("supervisor_id") or "")
            if not supervisor_id:
                raise ValueError("Cannot requeue without supervisor_id")
            # requeue_front also locks — use inner logic via nested RLock.
            return self.requeue_front(
                supervisor_id,
                kind,
                payload,
                queue_id=queue_id,
                blocked_on_global_cap=True,
            )

    # ------------------------------------------------------------------- drain
    def _drain_supervisor_locked(self, supervisor_id: str) -> List[str]:
        """Promote queued requests while under the wave cap. Caller holds lock."""
        pending_assigns: List[str] = []
        q = self._queues.get(supervisor_id)
        if not q:
            return pending_assigns

        cap = self._max_in_flight
        in_flight = self._in_flight.setdefault(supervisor_id, {})
        now = self._now()

        while q and len(in_flight) < cap:
            req = q[0]
            if req.status == WaveRequestStatus.CANCELLED:
                q.popleft()
                continue
            # Skip if somehow already promoted.
            if req.status not in (WaveRequestStatus.QUEUED,):
                q.popleft()
                continue

            # Handoff ownership: keep FIFO head when live intent exists and has
            # not expired (or an HTTP waiter is active). Never cancel merely
            # because wait_for_admission has not begun. Only explicit cancel
            # or lease expiry abandons.
            if req.kind == WaveKind.HANDOFF:
                has_waiter = req.waiter_count > 0
                lease_ok = (
                    req.live_intent
                    and req.lease_expires_at is not None
                    and req.lease_expires_at > now
                )
                if not (has_waiter or lease_ok):
                    q.popleft()
                    req.status = WaveRequestStatus.CANCELLED
                    req.live_intent = False
                    req.lease_expires_at = None
                    req.blocked_on_global_cap = False
                    req.admitted_event.set()
                    logger.info(
                        "Wave drain: cancelled abandoned handoff queue_id=%s "
                        "(no live intent/lease)",
                        req.queue_id,
                    )
                    continue

            reservation_id = _new_id("wres")
            slot = InFlightSlot(
                reservation_id=reservation_id,
                supervisor_id=supervisor_id,
                kind=req.kind,
                lease_expires_at=self._unbound_deadline(),
                queue_id=req.queue_id,
            )
            in_flight[reservation_id] = slot
            self._reservations[reservation_id] = slot
            q.popleft()
            req.status = WaveRequestStatus.ADMITTED
            req.reservation_id = reservation_id
            req.blocked_on_global_cap = False
            # Keep live_intent until bind/completion so disconnect can still
            # be distinguished; lease on the slot now owns unbound expiry.
            req.lease_expires_at = None
            req.admitted_event.set()

            if req.kind == WaveKind.HANDOFF:
                logger.info(
                    "Wave drain: handoff queue_id=%s admitted reservation=%s",
                    req.queue_id,
                    reservation_id,
                )
            else:
                # Assign: schedule create outside the lock via callback list.
                pending_assigns.append(req.queue_id)
                self._pending_assign_drains.append(req.queue_id)
                logger.info(
                    "Wave drain: assign queue_id=%s admitted reservation=%s " "(create scheduled)",
                    req.queue_id,
                    reservation_id,
                )

        if not q:
            self._queues.pop(supervisor_id, None)
        if not in_flight:
            self._in_flight.pop(supervisor_id, None)
        return pending_assigns

    def pop_pending_assign_drains(self) -> List[str]:
        """Return and clear queue_ids that need assign auto-start."""
        with self._lock:
            pending = list(self._pending_assign_drains)
            self._pending_assign_drains.clear()
            return pending

    def repend_assign_drains(self, queue_ids: List[str]) -> None:
        """Re-queue assign drain ids when no event loop was available yet."""
        with self._lock:
            for qid in queue_ids:
                if qid not in self._pending_assign_drains:
                    self._pending_assign_drains.append(qid)

    def get_request(self, queue_id: str) -> Optional[QueuedWaveRequest]:
        with self._lock:
            return self._requests.get(queue_id)

    def mark_assign_started(self, queue_id: str, terminal_id: str) -> bool:
        """Mark a drained assign as started and ensure the reservation is bound.

        Refuses CANCELLED / missing / non-admitted requests so a late create
        after unbound-lease expiry cannot resurrect cancelled state. Idempotent
        for an already-STARTED request with the same terminal_id.
        Returns True when the request was (or already is) STARTED.

        Binding is done inline (no nested ``bind_terminal`` reap) so an expired
        unbound lease cannot cancel this request mid-update under the same lock.
        """
        with self._lock:
            req = self._requests.get(queue_id)
            if req is None:
                logger.warning("Wave mark_assign_started no-op: unknown queue_id=%s", queue_id)
                return False
            if req.status == WaveRequestStatus.CANCELLED:
                logger.warning(
                    "Wave mark_assign_started refused: queue_id=%s is CANCELLED "
                    "(will not resurrect)",
                    queue_id,
                )
                return False
            if req.status == WaveRequestStatus.STARTED:
                if req.terminal_id and req.terminal_id != terminal_id:
                    logger.warning(
                        "Wave mark_assign_started refused: queue_id=%s already "
                        "started as %s (not %s)",
                        queue_id,
                        req.terminal_id,
                        terminal_id,
                    )
                    return False
                req.started_event.set()
                return True
            if req.status != WaveRequestStatus.ADMITTED:
                logger.warning(
                    "Wave mark_assign_started refused: queue_id=%s status=%s",
                    queue_id,
                    req.status.value,
                )
                return False
            if req.reservation_id:
                slot = self._reservations.get(req.reservation_id)
                if slot is None:
                    logger.warning(
                        "Wave mark_assign_started refused: queue_id=%s "
                        "reservation=%s missing (expired/released)",
                        queue_id,
                        req.reservation_id,
                    )
                    return False
                if slot.terminal_id and slot.terminal_id != terminal_id:
                    logger.warning(
                        "Wave mark_assign_started refused: reservation %s "
                        "already bound to %s (not %s)",
                        req.reservation_id,
                        slot.terminal_id,
                        terminal_id,
                    )
                    return False
                slot.terminal_id = terminal_id
                slot.lease_expires_at = None  # bound — never expires
                self._by_terminal[terminal_id] = req.reservation_id
            req.status = WaveRequestStatus.STARTED
            req.terminal_id = terminal_id
            req.live_intent = False
            req.lease_expires_at = None
            req.started_event.set()
            return True

    def wait_for_admission(self, queue_id: str, timeout: Optional[float] = None) -> WaveAdmitResult:
        """Block until a queued handoff (or assign) is admitted. Event-driven.

        On wait timeout while still ``queued``, the request is cancelled so a
        later drain cannot admit an orphaned handoff with no owner. Active
        waiters refresh / hold ownership independent of the queue lease.
        """
        with self._lock:
            self._reap_expired_locked()
            req = self._requests.get(queue_id)
            if req is None:
                return WaveAdmitResult(
                    status=WaveRequestStatus.RELEASED,
                    queue_id=queue_id,
                    message=f"Unknown wave queue_id: {queue_id}",
                )
            if req.status == WaveRequestStatus.CANCELLED:
                return WaveAdmitResult(
                    status=WaveRequestStatus.CANCELLED,
                    queue_id=queue_id,
                    message="Wave request was cancelled",
                )
            if (
                req.status
                in (
                    WaveRequestStatus.ADMITTED,
                    WaveRequestStatus.STARTED,
                )
                and req.reservation_id
            ):
                return WaveAdmitResult(
                    status=req.status,
                    reservation_id=req.reservation_id,
                    queue_id=queue_id,
                    terminal_id=req.terminal_id,
                    message="Already admitted",
                )
            req.waiter_count += 1
            # Refresh ownership while the HTTP wait is live.
            if req.kind == WaveKind.HANDOFF:
                req.live_intent = True
                # Hold at least through this wait attempt.
                hold_for = self._handoff_queue_lease_s
                if timeout is not None and timeout > 0:
                    hold_for = max(hold_for, float(timeout))
                req.lease_expires_at = self._now() + hold_for
            event = req.admitted_event

        try:
            ok = event.wait(timeout=timeout)
        finally:
            with self._lock:
                req = self._requests.get(queue_id)
                if req is not None and req.waiter_count > 0:
                    req.waiter_count -= 1

        with self._lock:
            self._reap_expired_locked()
            req = self._requests.get(queue_id)
            if req is None:
                return WaveAdmitResult(
                    status=WaveRequestStatus.RELEASED,
                    queue_id=queue_id,
                    message="Queue entry disappeared while waiting",
                )
            if req.status == WaveRequestStatus.CANCELLED:
                return WaveAdmitResult(
                    status=WaveRequestStatus.CANCELLED,
                    queue_id=queue_id,
                    message="Wave request was cancelled while waiting",
                )
            if not ok and req.status == WaveRequestStatus.QUEUED:
                # Abandon so a later drain cannot orphan-admit this handoff.
                self._cancel_request_locked(queue_id, reason=f"wait timeout ({timeout}s)")
                return WaveAdmitResult(
                    status=WaveRequestStatus.CANCELLED,
                    queue_id=queue_id,
                    message=f"Timed out waiting for wave admission ({timeout}s); cancelled",
                )
            return WaveAdmitResult(
                status=req.status,
                reservation_id=req.reservation_id,
                queue_id=queue_id,
                terminal_id=req.terminal_id,
                message="Wave slot admitted from queue",
            )

    # ----------------------------------------------------------------- inspect
    def snapshot(self, supervisor_id: str) -> Dict[str, Any]:
        """Test/ops view of one supervisor's wave state."""
        with self._lock:
            self._reap_expired_locked()
            in_flight = self._in_flight.get(supervisor_id, {})
            q = list(self._queues.get(supervisor_id, ()))
            return {
                "supervisor_id": supervisor_id,
                "max_in_flight": self._max_in_flight,
                "reconciled": self._reconciled,
                "in_flight_count": len(in_flight),
                "in_flight": [
                    {
                        "reservation_id": s.reservation_id,
                        "kind": s.kind.value,
                        "terminal_id": s.terminal_id,
                        "lease_expires_at": s.lease_expires_at,
                        "queue_id": s.queue_id,
                    }
                    for s in in_flight.values()
                ],
                "queued_count": len(q),
                "queued": [
                    {
                        "queue_id": r.queue_id,
                        "kind": r.kind.value,
                        "status": r.status.value,
                        "blocked_on_global_cap": r.blocked_on_global_cap,
                        "reservation_id": r.reservation_id,
                        "terminal_id": r.terminal_id,
                        "waiter_count": r.waiter_count,
                        "live_intent": r.live_intent,
                        "lease_expires_at": r.lease_expires_at,
                    }
                    for r in q
                ],
            }

    def in_flight_count(self, supervisor_id: str) -> int:
        with self._lock:
            return len(self._in_flight.get(supervisor_id, {}))

    def queued_count(self, supervisor_id: str) -> int:
        with self._lock:
            return len(self._queues.get(supervisor_id, ()))

    @staticmethod
    def _dedupe(ids: List[str]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for qid in ids:
            if qid not in seen:
                seen.add(qid)
                ordered.append(qid)
        return ordered


# Process singleton used by cao-server (and by MCP when CAO_WAVE_INPROCESS=true).
wave_service = WaveConcurrencyService()
