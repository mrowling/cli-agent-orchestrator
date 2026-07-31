"""D1 (swarm-economics): step start time journaling and result surfacing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.clients.database import (
    _migrate_workflow_run,
    _migrate_workflow_run_step,
)
from cli_agent_orchestrator.models.terminal import AgentStepResult, TerminalStatus
from cli_agent_orchestrator.models.workflow import RunState, StepState, WorkflowSpec, WorkflowStep
from cli_agent_orchestrator.models.workflow_runtime import (
    StepResult,
    WorkflowRunResult,
    duration_ms_between,
)
from cli_agent_orchestrator.services import workflow_journal
from cli_agent_orchestrator.services import workflow_service as ws


@pytest.fixture(autouse=True)
def _patched_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "wf.db"
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", db_path, raising=True)
    _migrate_workflow_run()
    _migrate_workflow_run_step()
    ws.run_registry.clear()
    ws._active_drives.clear()
    yield


def _ok() -> AgentStepResult:
    return AgentStepResult(terminal_id="t1", last_message="done", status=TerminalStatus.COMPLETED)


def _spec(step_ids=("s1",)) -> WorkflowSpec:
    return WorkflowSpec(
        name="wf",
        mode="sequential",
        steps=[
            WorkflowStep(
                id=sid,
                provider="claude_code",
                agent="dev",
                prompt="go",
                # No output_schema: avoids COMPLETED_UNVALIDATED when the mock
                # step returns plain text (D1 timing tests care about started_at).
            )
            for sid in step_ids
        ],
    )


def test_duration_ms_between_iso_timestamps():
    assert duration_ms_between("2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z") == 1000
    assert duration_ms_between(None, "2026-01-01T00:00:01Z") is None
    assert duration_ms_between("2026-01-01T00:00:00Z", None) is None


def test_workflow_run_result_duration_ms_computed():
    result = WorkflowRunResult(
        run_id="r1",
        workflow_name="wf",
        state=RunState.COMPLETED,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:10Z",
    )
    assert result.duration_ms == 10_000


@pytest.mark.asyncio
async def test_running_transition_journals_started_at(monkeypatch: pytest.MonkeyPatch):
    """D1: started_at is persisted on RUNNING and survives in the journal."""
    monkeypatch.setattr(ws, "run_agent_step", AsyncMock(return_value=_ok()))
    await ws.start_run(_spec(), {}, "run-timing")

    steps = {s.step_id: s for s in workflow_journal.get_steps("run-timing")}
    assert steps["s1"].started_at is not None
    assert steps["s1"].state == StepState.COMPLETED.value


@pytest.mark.asyncio
async def test_step_result_surfaces_timing_fields(monkeypatch: pytest.MonkeyPatch):
    """D1: WorkflowRunResult steps expose started_at, finished_at, duration_ms."""
    monkeypatch.setattr(ws, "run_agent_step", AsyncMock(return_value=_ok()))
    result = await ws.start_run(_spec(), {}, "run-result")

    assert len(result.steps) == 1
    step = result.steps[0]
    assert isinstance(step, StepResult)
    assert step.started_at is not None
    assert step.finished_at is not None
    assert step.duration_ms is not None
    assert step.duration_ms >= 0


@pytest.mark.asyncio
async def test_rebuild_restores_started_at_for_resume(monkeypatch: pytest.MonkeyPatch):
    """D1: resumed runs rebuild step started_at from the journal."""
    monkeypatch.setattr(ws, "run_agent_step", AsyncMock(return_value=_ok()))
    await ws.start_run(_spec(), {}, "run-resume")

    ws.run_registry.clear()
    record = ws._rebuild_record_from_journal("run-resume")
    assert record is not None
    assert record.step_states["s1"].started_at is not None
    assert record.step_states["s1"].updated_at is not None


def test_update_step_backfills_started_at_when_null():
    """D1: later journal writes backfill started_at when the row was first written without it."""
    run_id = "run-backfill"
    workflow_journal.insert_run(
        run_id,
        "wf",
        "{}",
        "{}",
        "running",
        "2026-01-01T00:00:00Z",
    )
    workflow_journal.insert_steps(run_id, [("s1", "pending")], "2026-01-01T00:00:00Z")

    # First write: FAILED without started_at (e.g. RUNNING stamp never journaled).
    workflow_journal.update_step(
        run_id,
        "s1",
        "failed",
        1,
        "2026-01-01T00:00:02Z",
        error="boom",
    )
    row = workflow_journal.get_step(run_id, "s1")
    assert row is not None
    assert row.started_at is None

    # Second write: backfill started_at on a later transition.
    workflow_journal.update_step(
        run_id,
        "s1",
        "failed",
        1,
        "2026-01-01T00:00:02Z",
        error="boom",
        started_at="2026-01-01T00:00:01Z",
    )
    row = workflow_journal.get_step(run_id, "s1")
    assert row is not None
    assert row.started_at == "2026-01-01T00:00:01Z"

    # COALESCE preserves an existing started_at — never overwritten.
    workflow_journal.update_step(
        run_id,
        "s1",
        "failed",
        2,
        "2026-01-01T00:00:03Z",
        error="boom",
        started_at="2026-01-01T00:09:00Z",
    )
    row = workflow_journal.get_step(run_id, "s1")
    assert row is not None
    assert row.started_at == "2026-01-01T00:00:01Z"


def test_append_step_backfills_started_at_on_conflict():
    """D1: append_step ON CONFLICT backfills started_at when the row still has NULL."""
    run_id = "run-append-backfill"
    workflow_journal.insert_run(
        run_id,
        "wf",
        "{}",
        "{}",
        "running",
        "2026-01-01T00:00:00Z",
    )
    workflow_journal.insert_steps(run_id, [("s1", "pending")], "2026-01-01T00:00:00Z")
    workflow_journal.update_step(run_id, "s1", "failed", 1, "2026-01-01T00:00:02Z", error="boom")

    workflow_journal.append_step(
        run_id,
        "s1",
        "failed",
        "2026-01-01T00:00:02Z",
        "fp1",
        started_at="2026-01-01T00:00:01Z",
    )
    row = workflow_journal.get_step(run_id, "s1")
    assert row is not None
    assert row.started_at == "2026-01-01T00:00:01Z"


@pytest.mark.asyncio
async def test_journal_step_backfills_after_failed_running_write(monkeypatch: pytest.MonkeyPatch):
    """D1: _journal_step passes started_at on COMPLETED even if RUNNING write was lost."""
    real_update = workflow_journal.update_step
    calls: list[tuple] = []

    def _track_update(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise RuntimeError("simulated RUNNING journal failure")
        return real_update(*args, **kwargs)

    monkeypatch.setattr(workflow_journal, "update_step", _track_update)
    monkeypatch.setattr(ws, "run_agent_step", AsyncMock(return_value=_ok()))
    await ws.start_run(_spec(), {}, "run-lost-running")

    row = workflow_journal.get_step("run-lost-running", "s1")
    assert row is not None
    assert row.started_at is not None
    assert row.state == StepState.COMPLETED.value
