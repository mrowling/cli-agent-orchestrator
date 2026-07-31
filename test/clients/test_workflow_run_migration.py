"""Tests for the workflow_run / workflow_run_step migrations (issue #312, Bolt 4 / N6).

Asserts ``_migrate_workflow_run`` and ``_migrate_workflow_run_step`` are zero-arg,
self-connecting, create the durable tables with the agreed E1/E2 columns, and are
idempotent (running twice is a no-op that preserves existing rows). NO loop columns
ship (Q4=B / B4-BR-12).

U3 (issue #312, script-tier journal extension, C3) additively appends
``tier``/``generation`` to ``workflow_run`` and ``call_fingerprint`` to
``workflow_run_step`` (domain-entities E1/E2). The column-set assertions below
are updated to include them; the defaults (``tier='yaml'``, ``generation='1'``,
``call_fingerprint=NULL``) preserve a pre-U3/YAML row's observable shape
(INV-1/INV-2).
"""

import sqlite3
from pathlib import Path

import pytest

from cli_agent_orchestrator.clients.database import (
    _migrate_workflow_run,
    _migrate_workflow_run_step,
)


@pytest.fixture
def patched_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "wf.db"
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", db_path, raising=True)
    return db_path


def _columns(db_path: Path, table: str) -> dict:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: r for r in rows}  # (cid, name, type, notnull, dflt_value, pk)


def test_workflow_run_columns(patched_db):
    _migrate_workflow_run()
    cols = _columns(patched_db, "workflow_run")
    assert set(cols) == {
        "run_id",
        "workflow_name",
        "spec_snapshot",
        "inputs_json",
        "state",
        "current_step_id",
        "started_at",
        "finished_at",
        "tier",
        "generation",
    }
    # run_id is the primary key; the nullable columns are current_step_id/finished_at.
    assert cols["run_id"][5] == 1
    assert cols["workflow_name"][3] == 1
    assert cols["spec_snapshot"][3] == 1
    assert cols["current_step_id"][3] == 0
    assert cols["finished_at"][3] == 0
    # U3 additive columns (E1): tier/generation default to the YAML-preserving values.
    assert cols["tier"][4] == "'yaml'"
    assert cols["generation"][4] == "'1'"


def test_workflow_run_no_loop_columns(patched_db):
    # B4-BR-12 / Q4=B: NO loop columns ship in N6 (they are N8's additive migration).
    _migrate_workflow_run()
    cols = _columns(patched_db, "workflow_run")
    assert "iteration_counter" not in cols
    assert "which_guard_fired" not in cols
    assert "iterations_run" not in cols


def test_workflow_run_step_columns(patched_db):
    _migrate_workflow_run_step()
    cols = _columns(patched_db, "workflow_run_step")
    assert set(cols) == {
        "run_id",
        "step_id",
        "state",
        "attempts",
        "output_json",
        "error",
        "updated_at",
        "call_fingerprint",
        "started_at",
    }
    # Composite PRIMARY KEY (run_id, step_id): both carry pk>0.
    assert cols["run_id"][5] > 0
    assert cols["step_id"][5] > 0
    # reprompted / terminal_id are deliberately NOT journaled (F3).
    assert "reprompted" not in cols
    assert "terminal_id" not in cols
    # U3 additive column (E2): defaults to NULL (INV-2). PRAGMA table_info reports
    # the literal default expression as the string "NULL", not Python None.
    assert cols["call_fingerprint"][4] == "NULL"
    # D1 additive column: started_at defaults to NULL for pre-D1 / never-run steps.
    assert cols["started_at"][4] == "NULL"


def test_workflow_run_step_started_at_additive_migration(patched_db):
    """D1: an older-schema DB without started_at migrates and preserves NULL on old rows."""
    # Simulate a pre-D1 schema (call_fingerprint present, started_at absent).
    with sqlite3.connect(str(patched_db)) as conn:
        conn.execute(
            "CREATE TABLE workflow_run_step ("
            "run_id TEXT NOT NULL, "
            "step_id TEXT NOT NULL, "
            "state TEXT NOT NULL, "
            "attempts INTEGER NOT NULL, "
            "output_json TEXT, "
            "error TEXT, "
            "updated_at TEXT NOT NULL, "
            "call_fingerprint TEXT DEFAULT NULL, "
            "PRIMARY KEY (run_id, step_id)"
            ")"
        )
        conn.execute(
            "INSERT INTO workflow_run_step "
            "(run_id, step_id, state, attempts, output_json, error, updated_at) "
            "VALUES ('r-old', 's1', 'completed', 1, NULL, NULL, '2026-01-01T00:00:05Z')"
        )
        conn.commit()

    _migrate_workflow_run_step()

    cols = _columns(patched_db, "workflow_run_step")
    assert "started_at" in cols

    with sqlite3.connect(str(patched_db)) as conn:
        row = conn.execute(
            "SELECT started_at FROM workflow_run_step WHERE run_id = ? AND step_id = ?",
            ("r-old", "s1"),
        ).fetchone()
    assert row is not None
    assert row[0] is None

    # A new RUNNING transition sets started_at via the journal write path.
    from cli_agent_orchestrator.services import workflow_journal

    workflow_journal.insert_steps("r-old", [("s2", "pending")], "2026-01-01T00:00:00Z")
    workflow_journal.update_step(
        "r-old",
        "s2",
        "running",
        0,
        "2026-01-01T00:00:01Z",
        started_at="2026-01-01T00:00:01Z",
    )
    row = workflow_journal.get_step("r-old", "s2")
    assert row is not None
    assert row.started_at == "2026-01-01T00:00:01Z"


def test_migrations_are_idempotent(patched_db):
    _migrate_workflow_run()
    _migrate_workflow_run_step()
    with sqlite3.connect(str(patched_db)) as conn:
        conn.execute(
            "INSERT INTO workflow_run "
            "(run_id, workflow_name, spec_snapshot, inputs_json, state, started_at) "
            "VALUES ('r1', 'wf', '{}', '{}', 'running', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
    # Second run must NOT drop/recreate the table.
    _migrate_workflow_run()
    _migrate_workflow_run_step()
    with sqlite3.connect(str(patched_db)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM workflow_run").fetchone()[0]
    assert count == 1


def test_zero_arg_callables(patched_db):
    # NB-1: both migrators are zero-arg, self-connecting.
    _migrate_workflow_run()
    _migrate_workflow_run_step()
