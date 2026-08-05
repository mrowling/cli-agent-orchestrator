"""ADT-6: per-supervisor wave concurrency queue."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.services.wave_concurrency import (
    WaveConcurrencyService,
    WaveKind,
    WaveReconcileError,
    WaveRequestStatus,
    wave_service,
)


class _FakeClock:
    """Injectable monotonic clock for lease tests (no sleeps)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _svc(**kwargs) -> WaveConcurrencyService:
    """Build a service already reconciled (unit tests skip live DB)."""
    svc = WaveConcurrencyService(**kwargs)
    svc.reconcile_in_flight_from_terminals([])
    return svc


@pytest.fixture(autouse=True)
def _reset_wave_singleton():
    wave_service.reset()
    wave_service.set_max_in_flight(3)
    wave_service.reconcile_in_flight_from_terminals([])
    yield
    wave_service.reset()
    wave_service.set_max_in_flight(3)
    wave_service.reconcile_in_flight_from_terminals([])


class TestWaveConcurrencyService:
    def test_default_cap_is_three(self):
        from cli_agent_orchestrator.constants import CAO_MAX_WAVE_IN_FLIGHT

        assert CAO_MAX_WAVE_IN_FLIGHT == 3
        assert wave_service.max_in_flight == 3

    def test_cap_two_assign_five_yields_two_active_three_queued(self):
        svc = _svc(max_in_flight=2)
        supervisor = "a1b2c3d4"
        results = [
            svc.try_admit(supervisor, WaveKind.ASSIGN, {"message": f"task-{i}", "n": i})
            for i in range(5)
        ]
        admitted = [r for r in results if r.status == WaveRequestStatus.ADMITTED]
        queued = [r for r in results if r.status == WaveRequestStatus.QUEUED]
        assert len(admitted) == 2
        assert len(queued) == 3
        assert svc.in_flight_count(supervisor) == 2
        assert svc.queued_count(supervisor) == 3
        # FIFO positions
        assert [r.position for r in queued] == [1, 2, 3]
        # Payloads preserved exactly
        for i, r in enumerate(queued):
            req = svc.get_request(r.queue_id)
            assert req is not None
            assert req.payload == {"message": f"task-{i + 2}", "n": i + 2}

    def test_delete_releases_and_drains_next_fifo(self):
        svc = _svc(max_in_flight=2)
        supervisor = "a1b2c3d4"
        a0 = svc.try_admit(supervisor, "assign", {"message": "first", "model": "m1"})
        a1 = svc.try_admit(supervisor, "assign", {"message": "second"})
        q2 = svc.try_admit(
            supervisor, "assign", {"message": "third-original", "model": "fable-5", "cwd": "/w"}
        )
        assert a0.status == WaveRequestStatus.ADMITTED
        assert a1.status == WaveRequestStatus.ADMITTED
        assert q2.status == WaveRequestStatus.QUEUED

        svc.bind_terminal(a0.reservation_id, "term0001")
        pending = svc.release(terminal_id="term0001")
        assert len(pending) == 1
        drained = svc.get_request(pending[0])
        assert drained is not None
        assert drained.status == WaveRequestStatus.ADMITTED
        assert drained.payload["message"] == "third-original"
        assert drained.payload["model"] == "fable-5"
        assert drained.payload["cwd"] == "/w"
        assert svc.in_flight_count(supervisor) == 2  # a1 still + drained
        assert svc.queued_count(supervisor) == 0

    def test_handoff_shares_budget_and_releases_on_completion(self):
        svc = _svc(max_in_flight=2)
        supervisor = "a1b2c3d4"
        assert svc.try_admit(supervisor, "assign", {}).status == WaveRequestStatus.ADMITTED
        h = svc.try_admit(supervisor, "handoff", {"message": "block"})
        assert h.status == WaveRequestStatus.ADMITTED
        q = svc.try_admit(supervisor, "assign", {"message": "waiting"})
        assert q.status == WaveRequestStatus.QUEUED

        # Handoff completion releases even without a bound terminal.
        pending = svc.release(reservation_id=h.reservation_id)
        assert pending == [q.queue_id]
        req = svc.get_request(q.queue_id)
        assert req.status == WaveRequestStatus.ADMITTED
        assert req.payload["message"] == "waiting"

    def test_queued_handoff_wait_is_event_driven(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        first = svc.try_admit(supervisor, "handoff", {})
        queued = svc.try_admit(supervisor, "handoff", {"message": "next"})
        assert queued.status == WaveRequestStatus.QUEUED

        result_box = {}

        def waiter():
            result_box["r"] = svc.wait_for_admission(queued.queue_id, timeout=2.0)

        t = threading.Thread(target=waiter)
        t.start()
        # Release first → drain wakes waiter via Event (no poll).
        svc.release(reservation_id=first.reservation_id)
        t.join(timeout=3.0)
        assert not t.is_alive()
        assert result_box["r"].status == WaveRequestStatus.ADMITTED
        assert result_box["r"].reservation_id is not None

    def test_global_cap_requeue_preserves_front_order(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        admitted = svc.try_admit(supervisor, "assign", {"message": "hold"})
        later = svc.try_admit(supervisor, "assign", {"message": "later"})
        assert later.status == WaveRequestStatus.QUEUED

        # Simulate: drain admitted a create that hit global cap → requeue front
        # of a brand-new request that was admitted then failed.
        # Free the hold slot by releasing, draining "later", then pretend
        # create failed and requeue "later" at front ahead of a new arrival.
        pending = svc.release(reservation_id=admitted.reservation_id)
        assert pending == [later.queue_id]
        drained = svc.get_request(later.queue_id)
        assert drained.reservation_id is not None

        qid = svc.release_reservation_and_requeue_front(
            drained.reservation_id,
            kind="assign",
            payload={"message": "later", "opts": True},
            queue_id=later.queue_id,
        )
        assert qid == later.queue_id
        req = svc.get_request(later.queue_id)
        assert req.payload["message"] == "later"
        assert req.payload["opts"] is True
        assert req.blocked_on_global_cap is True
        assert req.status == WaveRequestStatus.QUEUED

        # New arrival: drain promotes the requeued head first (FIFO), then
        # the newcomer queues behind it.
        newer = svc.try_admit(supervisor, "assign", {"message": "newer"})
        assert svc.get_request(later.queue_id).status == WaveRequestStatus.ADMITTED
        assert newer.status == WaveRequestStatus.QUEUED
        assert newer.position == 1
        snap = svc.snapshot(supervisor)
        assert snap["supervisor_id"] == supervisor

    def test_unrelated_supervisor_slots_are_independent(self):
        svc = _svc(max_in_flight=1)
        a = svc.try_admit("aaaaaaa1", "assign", {})
        b = svc.try_admit("bbbbbbb2", "assign", {})
        assert a.status == WaveRequestStatus.ADMITTED
        assert b.status == WaveRequestStatus.ADMITTED

    def test_on_terminal_deleted_triggers_drain(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        first = svc.try_admit(supervisor, "assign", {})
        queued = svc.try_admit(supervisor, "assign", {"message": "next"})
        svc.bind_terminal(first.reservation_id, "deadbeef")
        pending = svc.on_terminal_deleted("deadbeef")
        assert pending == [queued.queue_id]


class TestAssignWaveIntegration:
    """_assign_impl respects wave queue via wave_client (override autouse admit)."""

    @pytest.fixture(autouse=True)
    def _stub_assign_meta(self, monkeypatch):
        monkeypatch.setenv("CAO_TERMINAL_ID", "a1b2c3d4")
        monkeypatch.setenv("CAO_WAVE_INPROCESS", "true")

        def _get(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status.return_value = None
            if "working-directory" in str(url):
                resp.json.return_value = {"working_directory": "/tmp"}
            else:
                resp.json.return_value = {
                    "provider": "claude_code",
                    "session_name": "cao-test-session",
                    "allowed_tools": None,
                    "id": "a1b2c3d4",
                }
            return resp

        monkeypatch.setattr("cli_agent_orchestrator.mcp_server.server.requests.get", _get)
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.ENABLE_SENDER_ID_INJECTION", False
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server._resolve_child_allowed_tools",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.resolve_provider",
            lambda profile, fallback_provider=None: fallback_provider or "claude_code",
        )

    @patch("cli_agent_orchestrator.mcp_server.server._get_cleanup_nudge", return_value="")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    def test_cap_two_assign_five(self, mock_create, _nudge, monkeypatch):
        wave_service.reset()
        wave_service.set_max_in_flight(2)

        import cli_agent_orchestrator.mcp_server.wave_client as wc

        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.wave_client.try_admit", wc.try_admit
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.wave_client.bind_terminal",
            wc.bind_terminal,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.wave_client.release", wc.release
        )

        tids = [f"t{i:07d}" for i in range(5)]
        mock_create.side_effect = [(tid, "claude_code") for tid in tids[:2]]

        from cli_agent_orchestrator.mcp_server.server import _assign_impl

        results = [_assign_impl("developer", f"msg-{i}", model=f"m{i}") for i in range(5)]
        started = [r for r in results if r.get("status") == "started"]
        queued = [r for r in results if r.get("status") == "queued"]
        assert len(started) == 2
        assert len(queued) == 3
        assert mock_create.call_count == 2
        for r in queued:
            assert r["success"] is True
            assert r["terminal_id"] is None
            assert r["queue_id"]
        assert wave_service.in_flight_count("a1b2c3d4") == 2
        assert wave_service.queued_count("a1b2c3d4") == 3

        first_tid = started[0]["terminal_id"]
        pending = wave_service.release(terminal_id=first_tid)
        assert len(pending) == 1
        drained = wave_service.get_request(pending[0])
        assert drained.payload["message"] == "msg-2"
        assert drained.payload["model"] == "m2"

    @patch("cli_agent_orchestrator.mcp_server.server._get_cleanup_nudge", return_value="")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    def test_under_default_cap_behaves_like_immediate_assign(
        self, mock_create, _nudge, monkeypatch
    ):
        """Default cap=3: a single assign still returns started + terminal_id."""
        # Restore passthrough admit (no real wave service needed)
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.wave_client.try_admit",
            lambda *a, **k: {
                "status": "admitted",
                "reservation_id": "wres-x",
                "message": "ok",
            },
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.wave_client.bind_terminal",
            lambda *a, **k: None,
        )
        mock_create.return_value = ("deadbeef", "claude_code")
        from cli_agent_orchestrator.mcp_server.server import _assign_impl

        result = _assign_impl("developer", "one task")
        assert result["success"] is True
        assert result["terminal_id"] == "deadbeef"
        assert result.get("status", "started") == "started"


class TestHandoffWaveIntegration:
    @patch("cli_agent_orchestrator.mcp_server.server._get_cleanup_nudge", return_value="")
    @patch("cli_agent_orchestrator.mcp_server.server._resolve_handoff_provider")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    def test_handoff_waits_when_queued(self, mock_post, mock_provider, _nudge, monkeypatch):
        import asyncio

        from cli_agent_orchestrator.mcp_server.server import HandoffContext, _handoff_impl

        monkeypatch.setenv("CAO_TERMINAL_ID", "a1b2c3d4")
        mock_provider.return_value = HandoffContext(
            provider="claude_code",
            session_name="cao-s1",
            caller_id="a1b2c3d4",
            allowed_tools=None,
        )

        # First slot held by a prior admit; handoff will queue then wait.
        calls = {"admit": 0}

        def _admit(supervisor_id, kind, payload=None):
            calls["admit"] += 1
            if calls["admit"] == 1:
                return {
                    "status": "queued",
                    "queue_id": "wque-h1",
                    "position": 1,
                    "message": "queued",
                }
            return {"status": "admitted", "reservation_id": "wres-h1", "message": "ok"}

        def _wait(queue_id, timeout=None):
            assert queue_id == "wque-h1"
            return {
                "status": "admitted",
                "reservation_id": "wres-h1",
                "queue_id": queue_id,
                "message": "admitted from queue",
            }

        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.wave_client.try_admit", _admit
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.wave_client.wait_for_admission", _wait
        )
        released = []

        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.wave_client.release",
            lambda **kw: released.append(kw),
        )

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "terminal_id": "termhand",
            "last_message": "done",
            "status": "completed",
        }
        mock_post.return_value = resp

        result = asyncio.run(_handoff_impl("developer", "Do task"))
        assert result.success is True
        assert released and released[0].get("reservation_id") == "wres-h1"


class TestDeleteTerminalReleasesWave:
    def test_delete_terminal_calls_wave_release(self, monkeypatch):
        from cli_agent_orchestrator.services import terminal_service

        released = []

        class FakeWave:
            def on_terminal_deleted(self, terminal_id):
                released.append(terminal_id)
                return ["wque-x"]

        monkeypatch.setattr(
            "cli_agent_orchestrator.services.wave_concurrency.wave_service",
            FakeWave(),
        )
        scheduled = []
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.wave_drain.schedule_assign_drains",
            lambda ids: scheduled.extend(ids),
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
            lambda tid: None,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.provider_manager.cleanup_provider",
            lambda tid: None,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.db_delete_terminal",
            lambda tid: True,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.get_herdr_inbox_service",
            lambda: None,
        )

        assert terminal_service.delete_terminal("deadbeef") is True
        assert released == ["deadbeef"]
        assert scheduled == ["wque-x"]


class TestOrphanPreventionAndRelease:
    def test_wait_timeout_cancels_and_returns_to_baseline(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "handoff", {"message": "hold"})
        queued = svc.try_admit(
            supervisor,
            "handoff",
            {
                "message": "original-msg",
                "workspace": "worktree",
                "done_cmd": "true",
                "model": "m1",
                "options": {"x": 1},
            },
        )
        assert queued.status == WaveRequestStatus.QUEUED
        assert svc.queued_count(supervisor) == 1
        assert svc.in_flight_count(supervisor) == 1

        result = svc.wait_for_admission(queued.queue_id, timeout=0.05)
        assert result.status == WaveRequestStatus.CANCELLED
        assert svc.queued_count(supervisor) == 0
        assert svc.in_flight_count(supervisor) == 1  # only the hold remains

        # Later release must not resurrect the cancelled handoff.
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == []
        assert svc.in_flight_count(supervisor) == 0
        assert svc.queued_count(supervisor) == 0
        req = svc.get_request(queued.queue_id)
        assert req is not None
        assert req.status == WaveRequestStatus.CANCELLED
        # Payload preserved for inspection even after cancel.
        assert req.payload["message"] == "original-msg"
        assert req.payload["workspace"] == "worktree"
        assert req.payload["done_cmd"] == "true"
        assert req.payload["model"] == "m1"
        assert req.payload["options"] == {"x": 1}

    def test_handoff_live_intent_survives_drain_before_wait(self):
        """try_admit registers live intent atomically — drain must admit, not cancel."""
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "handoff", {})
        queued = svc.try_admit(supervisor, "handoff", {"message": "owned"})
        assert queued.status == WaveRequestStatus.QUEUED
        req = svc.get_request(queued.queue_id)
        assert req.live_intent is True
        assert req.waiter_count == 0  # HTTP wait not started yet
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == []
        req = svc.get_request(queued.queue_id)
        assert req.status == WaveRequestStatus.ADMITTED
        assert svc.in_flight_count(supervisor) == 1

    def test_expired_handoff_lease_is_cancelled_on_drain(self):
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            handoff_queue_lease_s=10.0,
            unbound_lease_s=10.0,
        )
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "handoff", {})
        orphan = svc.try_admit(supervisor, "handoff", {"message": "orphan"})
        assert orphan.status == WaveRequestStatus.QUEUED
        clock.advance(11.0)  # past queue lease; no HTTP waiter
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == []
        req = svc.get_request(orphan.queue_id)
        assert req.status == WaveRequestStatus.CANCELLED
        assert svc.in_flight_count(supervisor) == 0
        assert svc.queued_count(supervisor) == 0

    def test_unknown_release_does_not_global_drain(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "handoff", {})
        queued = svc.try_admit(supervisor, "handoff", {"message": "waiting"})
        # Live intent from try_admit is enough; waiter_count may still be 0.
        assert svc.get_request(queued.queue_id).live_intent is True

        pending = svc.release(terminal_id="deadbeef")  # unknown
        assert pending == []
        assert svc.in_flight_count(supervisor) == 1
        assert svc.queued_count(supervisor) == 1
        assert svc.get_request(queued.queue_id).status == WaveRequestStatus.QUEUED

        # Known release still drains the live-intent handoff head.
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == []
        assert svc.get_request(queued.queue_id).status == WaveRequestStatus.ADMITTED

    def test_global_cap_requeue_then_cancel_returns_baseline(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        admitted = svc.try_admit(
            supervisor,
            "handoff",
            {
                "message": "roundtrip",
                "workspace": "shared",
                "done_cmd": "true",
                "profile": "knight",
                "model": "opus",
                "options": {"flag": True},
            },
        )
        assert admitted.status == WaveRequestStatus.ADMITTED
        qid = svc.release_reservation_and_requeue_front(
            admitted.reservation_id,
            kind="handoff",
            payload={
                "message": "roundtrip",
                "workspace": "shared",
                "done_cmd": "true",
                "profile": "knight",
                "model": "opus",
                "options": {"flag": True},
                "supervisor_id": supervisor,
            },
        )
        assert svc.in_flight_count(supervisor) == 0
        assert svc.queued_count(supervisor) == 1
        req = svc.get_request(qid)
        assert req.payload["message"] == "roundtrip"
        assert req.payload["workspace"] == "shared"
        assert req.payload["done_cmd"] == "true"
        assert req.payload["profile"] == "knight"
        assert req.payload["model"] == "opus"
        assert req.payload["options"] == {"flag": True}
        assert req.blocked_on_global_cap is True

        # Abandoning the requeued handoff (caller returning failure / timeout)
        # must clear the queue so a later capacity event cannot orphan-admit.
        svc.cancel_request(qid, reason="caller abandoned after 429")
        assert svc.queued_count(supervisor) == 0
        assert svc.in_flight_count(supervisor) == 0
        # on_terminal_deleted must not admit cancelled request
        pending = svc.on_terminal_deleted("deadbeef")
        assert pending == []
        assert svc.in_flight_count(supervisor) == 0

    def test_payload_round_trip_queue_drain_preserves_fields(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        payload = {
            "message": "do the thing",
            "workspace": "worktree",
            "done_cmd": "pytest -q",
            "model": "sonnet",
            "agent_profile": "pawn",
            "options": {"cwd": "/repo", "env": {"A": "1"}},
        }
        hold = svc.try_admit(supervisor, "assign", {"message": "hold"})
        queued = svc.try_admit(supervisor, "assign", payload)
        assert queued.status == WaveRequestStatus.QUEUED
        req = svc.get_request(queued.queue_id)
        assert req.payload == payload

        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == [queued.queue_id]
        drained = svc.get_request(queued.queue_id)
        assert drained.status == WaveRequestStatus.ADMITTED
        assert drained.payload == payload

        # Simulate global-cap requeue of drained assign
        qid = svc.release_reservation_and_requeue_front(
            drained.reservation_id,
            kind="assign",
            payload=dict(payload, supervisor_id=supervisor),
            queue_id=queued.queue_id,
        )
        requeued = svc.get_request(qid)
        assert requeued.payload["message"] == "do the thing"
        assert requeued.payload["workspace"] == "worktree"
        assert requeued.payload["done_cmd"] == "pytest -q"
        assert requeued.payload["model"] == "sonnet"
        assert requeued.payload["agent_profile"] == "pawn"
        assert requeued.payload["options"] == {"cwd": "/repo", "env": {"A": "1"}}

    def test_reconcile_restores_in_flight_from_caller_id(self):
        svc = _svc(max_in_flight=3)
        created = svc.reconcile_in_flight_from_terminals(
            [
                {"id": "child001", "caller_id": "a1b2c3d4"},
                {"id": "child002", "caller_id": "a1b2c3d4"},
                {"id": "orphan00", "caller_id": None},
            ]
        )
        assert created == 2
        assert svc.in_flight_count("a1b2c3d4") == 2
        # Idempotent
        assert (
            svc.reconcile_in_flight_from_terminals([{"id": "child001", "caller_id": "a1b2c3d4"}])
            == 0
        )
        assert svc.in_flight_count("a1b2c3d4") == 2

    def test_on_terminal_deleted_recovers_global_cap_assign_only(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        # Park an assign on global cap (queued, blocked, no in-flight).
        svc.requeue_front(
            supervisor,
            "assign",
            {"message": "parked", "supervisor_id": supervisor},
            blocked_on_global_cap=True,
        )
        assert svc.in_flight_count(supervisor) == 0
        assert svc.queued_count(supervisor) == 1
        pending = svc.on_terminal_deleted("deadbeef")
        # After recovery the assign is admitted and scheduled for create.
        assert len(pending) == 1
        assert svc.in_flight_count(supervisor) == 1
        assert svc.get_request(pending[0]).status == WaveRequestStatus.ADMITTED


class TestLiveIntentToctou:
    """Reviewer MUST-FIX A: release between enqueue and wait must not cancel FIFO head."""

    def test_release_between_enqueue_and_wait_keeps_handoff_ahead_of_assign(self):
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            handoff_queue_lease_s=60.0,
            unbound_lease_s=30.0,
        )
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "handoff", {"message": "hold"})
        handoff = svc.try_admit(supervisor, "handoff", {"message": "first-handoff"})
        assert handoff.status == WaveRequestStatus.QUEUED
        assert svc.get_request(handoff.queue_id).live_intent is True
        assert svc.get_request(handoff.queue_id).waiter_count == 0

        # Sibling release before wait_for_admission begins (classic TOCTOU).
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == []
        assert svc.get_request(handoff.queue_id).status == WaveRequestStatus.ADMITTED

        # Later assign must queue behind the admitted handoff (cap=1).
        later = svc.try_admit(supervisor, "assign", {"message": "later-assign"})
        assert later.status == WaveRequestStatus.QUEUED
        assert svc.in_flight_count(supervisor) == 1
        snap = svc.snapshot(supervisor)
        assert snap["in_flight"][0]["kind"] == "handoff"
        assert later.position == 1

        # Wait observes the already-admitted handoff.
        result = svc.wait_for_admission(handoff.queue_id, timeout=1.0)
        assert result.status == WaveRequestStatus.ADMITTED
        assert result.reservation_id is not None

    def test_requeue_then_delete_preserves_handoff_ahead_of_later_assign(self):
        """Requeue-after-429 preserves live intent; delete must not cancel head."""
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            handoff_queue_lease_s=60.0,
            unbound_lease_s=30.0,
        )
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "assign", {"message": "hold"})
        svc.bind_terminal(hold.reservation_id, "hold0001")
        # Simulate handoff that hit 429: requeue at front with live intent.
        qid = svc.requeue_front(
            supervisor,
            "handoff",
            {"message": "rematch-me", "supervisor_id": supervisor},
            blocked_on_global_cap=True,
        )
        assert svc.get_request(qid).live_intent is True
        later = svc.try_admit(supervisor, "assign", {"message": "later-assign"})
        assert later.status == WaveRequestStatus.QUEUED
        # Capacity event between rematch requeue and wait_for_admission.
        pending = svc.on_terminal_deleted("hold0001")
        # Handoff head must admit first (live intent), not be cancelled.
        assert svc.get_request(qid).status == WaveRequestStatus.ADMITTED
        assert svc.get_request(later.queue_id).status == WaveRequestStatus.QUEUED
        assert svc.in_flight_count(supervisor) == 1
        assert later.queue_id not in pending


class TestUnboundLeaseExpiry:
    def test_unbound_reservation_expires_without_bind(self):
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            unbound_lease_s=5.0,
            handoff_queue_lease_s=60.0,
        )
        supervisor = "a1b2c3d4"
        admitted = svc.try_admit(supervisor, "assign", {"message": "die-before-create"})
        assert admitted.status == WaveRequestStatus.ADMITTED
        assert svc.in_flight_count(supervisor) == 1

        queued = svc.try_admit(supervisor, "assign", {"message": "next"})
        assert queued.status == WaveRequestStatus.QUEUED

        clock.advance(6.0)
        pending = svc.reap_expired()
        assert svc.in_flight_count(supervisor) == 1  # drained next
        assert pending == [queued.queue_id]
        assert svc.get_request(queued.queue_id).status == WaveRequestStatus.ADMITTED

    def test_bound_terminal_never_expires(self):
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            unbound_lease_s=5.0,
        )
        supervisor = "a1b2c3d4"
        admitted = svc.try_admit(supervisor, "assign", {})
        assert svc.bind_terminal(admitted.reservation_id, "term0001") is True
        clock.advance(100.0)
        pending = svc.reap_expired()
        assert pending == []
        assert svc.in_flight_count(supervisor) == 1

    def test_admitted_assign_unbound_lease_expires_and_cancels_request(self):
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            unbound_lease_s=5.0,
        )
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "assign", {"message": "hold"})
        queued = svc.try_admit(supervisor, "assign", {"message": "drain-me"})
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == [queued.queue_id]
        drained = svc.get_request(queued.queue_id)
        assert drained.status == WaveRequestStatus.ADMITTED
        assert drained.reservation_id is not None

        # Drain-start dies before create/bind — lease must free the slot.
        clock.advance(6.0)
        svc.reap_expired()
        assert svc.get_request(queued.queue_id).status == WaveRequestStatus.CANCELLED
        assert svc.in_flight_count(supervisor) == 0


class TestReconcileFailClosed:
    def test_failed_first_reconcile_retries_successfully(self):
        svc = WaveConcurrencyService(max_in_flight=2)
        assert svc.is_reconciled is False
        calls = {"n": 0}

        def _list():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db down")
            return [{"id": "child001", "caller_id": "a1b2c3d4"}]

        with patch(
            "cli_agent_orchestrator.clients.database.list_terminals_with_caller",
            _list,
        ):
            with pytest.raises(WaveReconcileError):
                svc.try_admit("a1b2c3d4", "assign", {"message": "nope"})
            assert svc.is_reconciled is False
            assert svc.in_flight_count("a1b2c3d4") == 0

            # Retry succeeds, restores live child, then queues/admits under cap.
            result = svc.try_admit("bbbbbbbb", "assign", {"message": "ok"})
            assert result.status == WaveRequestStatus.ADMITTED
            assert svc.is_reconciled is True
            assert svc.in_flight_count("a1b2c3d4") == 1  # restored child
            assert calls["n"] == 2

    def test_slow_reconcile_serializes_concurrent_admits_under_cap(self):
        """Concurrent first admits block behind reconcile and never exceed cap=2."""
        svc = WaveConcurrencyService(max_in_flight=2)
        barrier = threading.Barrier(4)
        started = threading.Event()
        release_list = threading.Event()
        results = []
        errors = []
        # Observed under the DB-list wait (reconcile still holds the service lock).
        mid_reconcile_in_flight = []

        def _list():
            # Safe: called while the wave lock is held; do not call back into svc.
            mid_reconcile_in_flight.append(len(svc._in_flight.get("a1b2c3d4", {})))
            started.set()
            assert release_list.wait(timeout=5.0), "reconcile was not released"
            return [
                {"id": "child001", "caller_id": "a1b2c3d4"},
                {"id": "child002", "caller_id": "a1b2c3d4"},
            ]

        def _admit(i: int):
            try:
                barrier.wait(timeout=5.0)
                results.append(svc.try_admit("a1b2c3d4", "assign", {"message": f"t{i}", "n": i}))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with patch(
            "cli_agent_orchestrator.clients.database.list_terminals_with_caller",
            _list,
        ):
            threads = [threading.Thread(target=_admit, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            assert started.wait(timeout=5.0)
            # While reconcile holds the lock, do not call svc methods (would deadlock).
            assert mid_reconcile_in_flight == [0]
            release_list.set()
            for t in threads:
                t.join(timeout=5.0)
                assert not t.is_alive()

        assert errors == []
        assert svc.is_reconciled is True
        # Two live children restored → cap full → all four admits must be queued.
        assert svc.in_flight_count("a1b2c3d4") == 2
        admitted = [r for r in results if r.status == WaveRequestStatus.ADMITTED]
        queued = [r for r in results if r.status == WaveRequestStatus.QUEUED]
        assert len(admitted) == 0
        assert len(queued) == 4
        assert svc.queued_count("a1b2c3d4") == 4


class TestHandoffWaveVsGlobalCap:
    def test_handoff_completion_releases_wave_slot_while_terminal_may_remain(self):
        """Wave budget releases on handoff completion; global cap still counts terminal.

        Documented distinction: diagnostic preserve keeps the terminal row (global
        CAO_MAX_ACTIVE_TERMINALS), but the wave reservation is released so siblings
        can proceed.
        """
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        handoff = svc.try_admit(supervisor, "handoff", {"message": "diag"})
        assert handoff.status == WaveRequestStatus.ADMITTED
        svc.bind_terminal(handoff.reservation_id, "diag0001")
        # Logical completion releases wave slot even though terminal still "exists".
        pending = svc.release(reservation_id=handoff.reservation_id)
        assert svc.in_flight_count(supervisor) == 0
        # Terminal id is no longer mapped to a wave reservation.
        assert svc.on_terminal_deleted("diag0001") == []
        # A subsequent assign can use the wave slot immediately.
        nxt = svc.try_admit(supervisor, "assign", {"message": "next"})
        assert nxt.status == WaveRequestStatus.ADMITTED
        assert pending == []


class TestBindBeforeSlowCreate:
    """BLOCKER 1: bind before expensive create so unbound lease cannot exceed cap."""

    def test_bind_before_clock_advance_keeps_sibling_queued_under_cap(self):
        """Admit → bind immediately → advance past unbound lease → sibling stays queued.

        Live tracked (in-flight) count must remain <= cap; bound reservations are
        lease-free until delete/handoff completion.
        """
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            unbound_lease_s=5.0,
        )
        supervisor = "a1b2c3d4"
        admitted = svc.try_admit(supervisor, "assign", {"message": "slow-create"})
        assert admitted.status == WaveRequestStatus.ADMITTED
        assert admitted.reservation_id is not None

        # Bind BEFORE any slow create / clock advance (create_terminal contract).
        assert svc.bind_terminal(admitted.reservation_id, "term0001") is True
        snap = svc.snapshot(supervisor)
        assert snap["in_flight"][0]["lease_expires_at"] is None  # lease-free

        sibling = svc.try_admit(supervisor, "assign", {"message": "sibling"})
        assert sibling.status == WaveRequestStatus.QUEUED
        assert svc.in_flight_count(supervisor) == 1
        assert svc.queued_count(supervisor) == 1

        clock.advance(30.0)  # well past unbound lease TTL
        pending = svc.reap_expired()
        assert pending == []
        assert svc.in_flight_count(supervisor) <= 1
        assert svc.in_flight_count(supervisor) == 1
        assert svc.get_request(sibling.queue_id).status == WaveRequestStatus.QUEUED
        # Bound slot still maps to the live terminal.
        assert svc.snapshot(supervisor)["in_flight"][0]["terminal_id"] == "term0001"

    def test_late_bind_after_expiry_fails_and_mark_started_refuses_cancelled(self):
        """Unbound lease expiry frees the slot; late bind fails; mark_assign_started
        must not resurrect CANCELLED (assign drain path).
        """
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            unbound_lease_s=5.0,
        )
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "assign", {"message": "hold"})
        queued = svc.try_admit(supervisor, "assign", {"message": "drain-me"})
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == [queued.queue_id]
        drained = svc.get_request(queued.queue_id)
        assert drained.status == WaveRequestStatus.ADMITTED
        rid = drained.reservation_id
        assert rid is not None

        clock.advance(6.0)
        svc.reap_expired()
        assert svc.get_request(queued.queue_id).status == WaveRequestStatus.CANCELLED
        assert svc.in_flight_count(supervisor) == 0

        # Late bind after expiry must fail (create_terminal aborts before side effects).
        assert svc.bind_terminal(rid, "termdead1") is False
        # mark_assign_started must refuse — never resurrect CANCELLED.
        assert svc.mark_assign_started(queued.queue_id, "termdead1") is False
        assert svc.get_request(queued.queue_id).status == WaveRequestStatus.CANCELLED
        assert svc.in_flight_count(supervisor) == 0

    def test_handoff_bind_before_slow_run_step_keeps_sibling_queued(self):
        """Handoff/run-step path: early bind + clock advance must not free the slot."""
        clock = _FakeClock()
        svc = _svc(
            max_in_flight=1,
            clock=clock,
            unbound_lease_s=5.0,
        )
        supervisor = "a1b2c3d4"
        handoff = svc.try_admit(supervisor, "handoff", {"message": "slow-step"})
        assert handoff.status == WaveRequestStatus.ADMITTED
        assert svc.bind_terminal(handoff.reservation_id, "hand0001") is True

        sibling = svc.try_admit(supervisor, "assign", {"message": "after"})
        assert sibling.status == WaveRequestStatus.QUEUED

        clock.advance(60.0)
        assert svc.reap_expired() == []
        assert svc.in_flight_count(supervisor) == 1
        assert svc.get_request(sibling.queue_id).status == WaveRequestStatus.QUEUED

        # Handoff completion releases; sibling then drains.
        pending = svc.release(reservation_id=handoff.reservation_id)
        assert svc.in_flight_count(supervisor) == 1
        assert pending == [sibling.queue_id]
        assert svc.get_request(sibling.queue_id).status == WaveRequestStatus.ADMITTED


class TestCreateTerminalEarlyBind:
    """create_terminal binds before workspace/tmux; late bind aborts with no side effects."""

    def test_expired_reservation_aborts_before_workspace_or_tmux(self):
        import asyncio
        from unittest.mock import MagicMock

        from cli_agent_orchestrator.services.terminal_service import (
            WaveReservationBindError,
            create_terminal,
        )

        clock = _FakeClock()
        svc = _svc(max_in_flight=1, clock=clock, unbound_lease_s=5.0)
        admitted = svc.try_admit("a1b2c3d4", "assign", {"message": "late"})
        rid = admitted.reservation_id
        clock.advance(6.0)
        svc.reap_expired()
        assert svc.bind_terminal(rid, "deadbeef") is False

        workspace_calls = []
        tmux_calls = []

        async def _run():
            with patch(
                "cli_agent_orchestrator.services.wave_concurrency.wave_service",
                svc,
            ):
                with patch(
                    "cli_agent_orchestrator.workspaces.factory.create_workspace_for_terminal",
                    side_effect=lambda **k: workspace_calls.append(k) or MagicMock(),
                ):
                    with patch(
                        "cli_agent_orchestrator.services.terminal_service.get_backend"
                    ) as mock_backend:
                        mock_backend.return_value = MagicMock()

                        def _create_session(*a, **k):
                            tmux_calls.append(("session", a, k))

                        def _create_window(*a, **k):
                            tmux_calls.append(("window", a, k))
                            return "win"

                        mock_backend.return_value.create_session.side_effect = _create_session
                        mock_backend.return_value.create_window.side_effect = _create_window
                        mock_backend.return_value.session_exists.return_value = False
                        with pytest.raises(WaveReservationBindError):
                            await create_terminal(
                                "kiro_cli",
                                "developer",
                                new_session=True,
                                wave_reservation_id=rid,
                            )

        asyncio.run(_run())
        assert workspace_calls == []
        assert tmux_calls == []

    def test_early_bind_survives_slow_create_clock_advance(self):
        """Bind inside create_terminal clears lease before workspace work."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from cli_agent_orchestrator.services.terminal_service import create_terminal
        from cli_agent_orchestrator.workspaces.models import WorkspaceInfo

        clock = _FakeClock()
        svc = _svc(max_in_flight=1, clock=clock, unbound_lease_s=5.0)
        admitted = svc.try_admit("a1b2c3d4", "assign", {"message": "slow"})
        rid = admitted.reservation_id
        sibling_results = []

        async def _run():
            with patch(
                "cli_agent_orchestrator.services.wave_concurrency.wave_service",
                svc,
            ):
                with patch(
                    "cli_agent_orchestrator.workspaces.factory.create_workspace_for_terminal",
                ) as mock_ws:
                    # Simulate slow create: advance clock during workspace creation.
                    def _slow_ws(**kwargs):
                        clock.advance(30.0)
                        # Sibling admit during slow create must stay queued.
                        sibling_results.append(
                            svc.try_admit("a1b2c3d4", "assign", {"message": "sib"})
                        )
                        assert svc.in_flight_count("a1b2c3d4") == 1
                        return WorkspaceInfo(backend="shared", path="/tmp/ws", name="ws")

                    mock_ws.side_effect = _slow_ws
                    with patch(
                        "cli_agent_orchestrator.workspaces.registry.persist_workspace_lifecycle"
                    ):
                        with patch(
                            "cli_agent_orchestrator.services.terminal_service.get_backend"
                        ) as mock_backend:
                            be = MagicMock()
                            be.session_exists.return_value = False
                            be.supports_event_inbox.return_value = True
                            mock_backend.return_value = be
                            with patch(
                                "cli_agent_orchestrator.services.terminal_service.db_create_terminal"
                            ):
                                with patch(
                                    "cli_agent_orchestrator.services.terminal_service.provider_manager"
                                ) as pm:
                                    prov = MagicMock()
                                    prov.initialize = AsyncMock()
                                    prov.shell_baseline = None
                                    pm.create_provider.return_value = prov
                                    with patch(
                                        "cli_agent_orchestrator.services.terminal_service.load_agent_profile",
                                        return_value=None,
                                    ):
                                        with patch(
                                            "cli_agent_orchestrator.services.terminal_service.dispatch_plugin_event"
                                        ):
                                            with patch(
                                                "cli_agent_orchestrator.services.terminal_service.get_herdr_inbox_service",
                                                return_value=None,
                                            ):
                                                term = await create_terminal(
                                                    "kiro_cli",
                                                    "developer",
                                                    new_session=True,
                                                    wave_reservation_id=rid,
                                                )
                                                assert term.id
                                                assert svc.in_flight_count("a1b2c3d4") == 1
                                                snap = svc.snapshot("a1b2c3d4")
                                                assert (
                                                    snap["in_flight"][0]["lease_expires_at"] is None
                                                )
                                                assert (
                                                    snap["in_flight"][0]["terminal_id"] == term.id
                                                )

        asyncio.run(_run())
        assert sibling_results
        assert sibling_results[0].status == WaveRequestStatus.QUEUED
        assert svc.queued_count("a1b2c3d4") == 1

    def test_create_exception_after_bind_releases_reservation_and_no_side_effects(self):
        """Bind succeeds then workspace/tmux fails — reservation freed, no orphan child."""
        import asyncio
        from unittest.mock import MagicMock

        from cli_agent_orchestrator.services.terminal_service import create_terminal

        clock = _FakeClock()
        svc = _svc(max_in_flight=1, clock=clock, unbound_lease_s=5.0)
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "assign", {"message": "hold"})
        queued = svc.try_admit(supervisor, "assign", {"message": "fail-after-bind"})
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == [queued.queue_id]
        drained = svc.get_request(queued.queue_id)
        rid = drained.reservation_id

        workspace_calls = []
        tmux_calls = []
        db_calls = []

        def _boom(**k):
            workspace_calls.append(k)
            raise RuntimeError("workspace boom")

        async def _run():
            with patch(
                "cli_agent_orchestrator.services.wave_concurrency.wave_service",
                svc,
            ):
                with patch(
                    "cli_agent_orchestrator.workspaces.factory.create_workspace_for_terminal",
                    side_effect=_boom,
                ):
                    with patch(
                        "cli_agent_orchestrator.services.terminal_service.get_backend"
                    ) as mock_backend:
                        mock_backend.return_value = MagicMock()

                        def _create_session(*a, **k):
                            tmux_calls.append(("session", a, k))

                        mock_backend.return_value.create_session.side_effect = _create_session
                        mock_backend.return_value.session_exists.return_value = False
                        with patch(
                            "cli_agent_orchestrator.services.terminal_service.db_delete_terminal",
                            side_effect=lambda tid: db_calls.append(tid) or True,
                        ):
                            with pytest.raises(RuntimeError, match="workspace boom"):
                                await create_terminal(
                                    "kiro_cli",
                                    "developer",
                                    new_session=True,
                                    wave_reservation_id=rid,
                                )

        asyncio.run(_run())
        assert len(workspace_calls) == 1
        assert tmux_calls == []
        assert db_calls  # rollback attempted
        assert svc.in_flight_count(supervisor) == 0
        assert svc.get_request(queued.queue_id).status == WaveRequestStatus.ADMITTED


class TestCancelAfterEarlyBind:
    """Cancel after early bind must not release bound reservations."""

    def test_assign_queue_drain_bind_cancel_refuses_then_delete_admits_fifo(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "assign", {"message": "hold"})
        sibling = svc.try_admit(supervisor, "assign", {"message": "drained"})
        assert sibling.status == WaveRequestStatus.QUEUED
        pending = svc.release(reservation_id=hold.reservation_id)
        assert pending == [sibling.queue_id]
        drained = svc.get_request(sibling.queue_id)
        assert drained.status == WaveRequestStatus.ADMITTED
        assert svc.bind_terminal(drained.reservation_id, "term0001") is True
        assert svc.get_request(sibling.queue_id).terminal_id == "term0001"

        late = svc.try_admit(supervisor, "assign", {"message": "late"})
        assert late.status == WaveRequestStatus.QUEUED
        assert svc.cancel_request(sibling.queue_id, reason="late cancel") == []
        assert svc.get_request(sibling.queue_id).status == WaveRequestStatus.ADMITTED
        assert svc.in_flight_count(supervisor) == 1
        assert svc.queued_count(supervisor) == 1

        drain2 = svc.on_terminal_deleted("term0001")
        assert drain2 == [late.queue_id]
        assert svc.get_request(late.queue_id).status == WaveRequestStatus.ADMITTED
        assert svc.in_flight_count(supervisor) == 1

    def test_handoff_bind_cancel_refuses_then_completion_admits_fifo(self):
        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "handoff", {"message": "hold"})
        handoff = svc.try_admit(supervisor, "handoff", {"message": "slow-step"})
        assert handoff.status == WaveRequestStatus.QUEUED
        sibling = svc.try_admit(supervisor, "assign", {"message": "after"})
        assert sibling.status == WaveRequestStatus.QUEUED
        svc.release(reservation_id=hold.reservation_id)
        drained = svc.get_request(handoff.queue_id)
        assert drained.status == WaveRequestStatus.ADMITTED
        assert svc.bind_terminal(drained.reservation_id, "hand0001") is True
        assert svc.get_request(handoff.queue_id).terminal_id == "hand0001"

        assert svc.cancel_request(handoff.queue_id, reason="abort") == []
        assert svc.get_request(handoff.queue_id).status == WaveRequestStatus.ADMITTED
        assert svc.in_flight_count(supervisor) == 1
        assert svc.get_request(sibling.queue_id).status == WaveRequestStatus.QUEUED

        release_pending = svc.release(
            reservation_id=svc.get_request(handoff.queue_id).reservation_id
        )
        assert release_pending == [sibling.queue_id]
        assert svc.get_request(sibling.queue_id).status == WaveRequestStatus.ADMITTED


class TestAssignDrainMarkStartedFailClosed:
    def test_mark_assign_started_false_deletes_terminal_and_releases_slot(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from cli_agent_orchestrator.models.provider import ProviderType
        from cli_agent_orchestrator.models.terminal import Terminal
        from cli_agent_orchestrator.services.wave_drain import _drain_assign

        svc = _svc(max_in_flight=1)
        supervisor = "a1b2c3d4"
        hold = svc.try_admit(supervisor, "assign", {"message": "hold"})
        queued = svc.try_admit(
            supervisor,
            "assign",
            {
                "provider": "kiro_cli",
                "agent_profile": "developer",
                "message": "go",
            },
        )
        assert queued.status == WaveRequestStatus.QUEUED
        svc.release(reservation_id=hold.reservation_id)
        qid = queued.queue_id
        rid = svc.get_request(qid).reservation_id
        assert rid is not None

        fake_term = Terminal(
            id="aabbcc01",
            name="win",
            session_name="cao-test",
            agent_profile="developer",
            provider=ProviderType.KIRO_CLI,
        )
        deleted = []
        inbox = []

        async def _create(**kwargs):
            wave_reservation_id = kwargs.get("wave_reservation_id")
            if wave_reservation_id:
                svc.bind_terminal(wave_reservation_id, fake_term.id)
            return fake_term

        def _delete(tid: str) -> bool:
            deleted.append(tid)
            svc.on_terminal_deleted(tid)
            return True

        async def _run():
            with patch("cli_agent_orchestrator.services.wave_drain.wave_service", svc):
                with patch.object(svc, "mark_assign_started", return_value=False):
                    with patch(
                        "cli_agent_orchestrator.services.terminal_service.create_terminal",
                        new=AsyncMock(side_effect=_create),
                    ):
                        with patch(
                            "cli_agent_orchestrator.services.terminal_service.delete_terminal",
                            side_effect=_delete,
                        ):
                            with patch(
                                "cli_agent_orchestrator.services.wave_drain.create_inbox_message",
                                side_effect=lambda **kw: inbox.append(kw),
                            ):
                                await _drain_assign(qid)

        asyncio.run(_run())
        assert deleted == ["aabbcc01"]
        assert inbox
        assert svc.in_flight_count(supervisor) == 0
