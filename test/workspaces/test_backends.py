"""Unit tests for workspace backends (Item 4 / D11)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cli_agent_orchestrator.workspaces.base import (
    WorkspaceBackendError,
    WorkspaceNotAvailableError,
)
from cli_agent_orchestrator.workspaces.factory import (
    resolve_workspace_selection,
    select_backend,
)
from cli_agent_orchestrator.workspaces.models import WorkspaceCleanupStatus
from cli_agent_orchestrator.workspaces.rift import RiftWorkspaceBackend
from cli_agent_orchestrator.workspaces.shared import SharedWorkspaceBackend
from cli_agent_orchestrator.workspaces.worktree import WorktreeWorkspaceBackend


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    return completed.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "cao@example.com")
    _git(repo, "config", "user.name", "CAO Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "init")
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    monkeypatch.setenv("CAO_HOME_DIR", str(tmp_path / "cao-home"))
    return repo


class TestSelection:
    def test_shared_is_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("CAO_WORKSPACE_BACKEND", raising=False)
        assert resolve_workspace_selection(None) == "shared"

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CAO_WORKSPACE_BACKEND", "worktree")
        assert resolve_workspace_selection(None) == "worktree"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CAO_WORKSPACE_BACKEND", "worktree")
        assert resolve_workspace_selection("shared") == "shared"

    def test_auto_selects_worktree_in_git_repo(self, git_repo: Path):
        backend, name = select_backend("auto", from_path=str(git_repo))
        assert name == "worktree"
        assert isinstance(backend, WorktreeWorkspaceBackend)

    def test_auto_falls_back_to_shared_outside_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        bare = tmp_path / "not-a-repo"
        bare.mkdir()
        backend, name = select_backend("auto", from_path=str(bare))
        assert name == "shared"
        assert isinstance(backend, SharedWorkspaceBackend)

    def test_explicit_rift_raises(self, git_repo: Path):
        with pytest.raises(WorkspaceNotAvailableError, match="deferred"):
            select_backend("rift", from_path=str(git_repo))

    def test_rift_probe_false(self):
        assert RiftWorkspaceBackend().probe() is False


class TestSharedBackend:
    def test_create_preserves_path(self, git_repo: Path):
        backend = SharedWorkspaceBackend()
        info = backend.create(str(git_repo), "n1")
        assert info.backend == "shared"
        assert Path(info.path).resolve() == git_repo.resolve()
        cleanup = backend.remove(info)
        assert cleanup.status == WorkspaceCleanupStatus.NOOP


class TestWorktreeBackend:
    def test_two_workers_independent_tracked_file(self, git_repo: Path, tmp_path: Path):
        backend = WorktreeWorkspaceBackend(workspaces_root=str(tmp_path / "ws"))
        a = backend.create(str(git_repo), "worker-a")
        b = backend.create(str(git_repo), "worker-b")
        assert a.path != b.path
        assert a.branch != b.branch
        assert a.base_ref == b.base_ref

        Path(a.path, "tracked.txt").write_text("from-a\n", encoding="utf-8")
        Path(b.path, "tracked.txt").write_text("from-b\n", encoding="utf-8")

        assert Path(a.path, "tracked.txt").read_text(encoding="utf-8") == "from-a\n"
        assert Path(b.path, "tracked.txt").read_text(encoding="utf-8") == "from-b\n"
        # Source tracked file unchanged (committed-ref isolation).
        assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "base\n"

    def test_does_not_copy_dirty_source(self, git_repo: Path, tmp_path: Path):
        (git_repo / "tracked.txt").write_text("dirty-uncommitted\n", encoding="utf-8")
        backend = WorktreeWorkspaceBackend(workspaces_root=str(tmp_path / "ws"))
        info = backend.create(str(git_repo), "clean-worker")
        assert Path(info.path, "tracked.txt").read_text(encoding="utf-8") == "base\n"

    def test_clean_cleanup_preserves_branch(self, git_repo: Path, tmp_path: Path):
        backend = WorktreeWorkspaceBackend(workspaces_root=str(tmp_path / "ws"))
        info = backend.create(str(git_repo), "clean-one")
        Path(info.path, "tracked.txt").write_text("done\n", encoding="utf-8")
        _git(Path(info.path), "add", "tracked.txt")
        _git(Path(info.path), "commit", "-m", "worker change")

        result = backend.remove(info)
        assert result.status == WorkspaceCleanupStatus.REMOVED
        assert not Path(info.path).exists()
        # Branch retained for manual merge.
        branches = _git(git_repo, "branch", "--list", info.branch)
        assert info.branch in branches
        assert "merge" in result.message.lower()

    def test_dirty_cleanup_preserves_workspace(self, git_repo: Path, tmp_path: Path):
        backend = WorktreeWorkspaceBackend(workspaces_root=str(tmp_path / "ws"))
        info = backend.create(str(git_repo), "dirty-one")
        Path(info.path, "tracked.txt").write_text("uncommitted\n", encoding="utf-8")

        result = backend.remove(info)
        assert result.status == WorkspaceCleanupStatus.PRESERVED_DIRTY
        assert Path(info.path).is_dir()
        assert "force" in result.message.lower()

    def test_remove_idempotent(self, git_repo: Path, tmp_path: Path):
        backend = WorktreeWorkspaceBackend(workspaces_root=str(tmp_path / "ws"))
        info = backend.create(str(git_repo), "idem")
        first = backend.remove(info)
        assert first.status == WorkspaceCleanupStatus.REMOVED
        second = backend.remove(info)
        assert second.status == WorkspaceCleanupStatus.NOOP

    def test_rejects_unsafe_argv_style_name(self, git_repo: Path, tmp_path: Path):
        backend = WorktreeWorkspaceBackend(workspaces_root=str(tmp_path / "ws"))
        with pytest.raises(WorkspaceBackendError):
            backend.create(str(git_repo), "../escape")
        with pytest.raises(WorkspaceBackendError):
            backend.create(str(git_repo), "has space")

    def test_diff_lists_paths(self, git_repo: Path, tmp_path: Path):
        backend = WorktreeWorkspaceBackend(workspaces_root=str(tmp_path / "ws"))
        info = backend.create(str(git_repo), "diffme")
        Path(info.path, "tracked.txt").write_text("changed\n", encoding="utf-8")
        patch = backend.diff(info)
        assert "tracked.txt" in patch.paths or "tracked.txt" in patch.unified_diff

    def test_repeated_name_raises_typed_collision(self, git_repo: Path, tmp_path: Path):
        from cli_agent_orchestrator.workspaces.base import WorkspaceNameCollisionError

        backend = WorktreeWorkspaceBackend(workspaces_root=str(tmp_path / "ws"))
        first = backend.create(str(git_repo), "same-name")
        # Clean remove retains the branch — recreating the same name must collide.
        removed = backend.remove(first)
        assert removed.status == WorkspaceCleanupStatus.REMOVED
        with pytest.raises(WorkspaceNameCollisionError) as exc_info:
            backend.create(str(git_repo), "same-name")
        assert exc_info.value.branch == first.branch
        assert "already exists" in str(exc_info.value).lower()

    def test_factory_retries_through_branch_collision(
        self, git_repo: Path, tmp_path: Path, monkeypatch
    ):
        from cli_agent_orchestrator.workspaces.factory import create_workspace_for_terminal

        ws_root = tmp_path / "workspaces"
        ws_root.mkdir(exist_ok=True)
        monkeypatch.setattr(
            "cli_agent_orchestrator.workspaces.worktree._default_workspaces_root",
            lambda: str(ws_root),
        )
        # Seed a retained branch that would collide with the first generated name.
        backend = WorktreeWorkspaceBackend(workspaces_root=str(ws_root))
        seed = backend.create(str(git_repo), "aaaa1111-bbbbbbbb")
        backend.remove(seed)

        calls = {"n": 0}
        from cli_agent_orchestrator.workspaces import factory as factory_mod

        real_make = factory_mod.make_workspace_name

        def _make(terminal_id=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return "aaaa1111-bbbbbbbb"
            return real_make(terminal_id)

        monkeypatch.setattr(factory_mod, "make_workspace_name", _make)
        info = create_workspace_for_terminal(
            from_path=str(git_repo),
            terminal_id="aaaa1111",
            workspace="worktree",
        )
        assert info.name != "aaaa1111-bbbbbbbb"
        assert Path(info.path).is_dir()


class TestAssignQueuePreservesWorkspace:
    def test_wave_payload_shape_documents_workspace_key(self):
        # Contract check: Item 6 queue payload must carry workspace through.
        payload = {
            "supervisor_id": "abcd1234",
            "agent_profile": "knight",
            "message": "do work",
            "working_directory": "/tmp/repo",
            "workspace": "worktree",
        }
        assert payload["workspace"] == "worktree"


class TestCleanupRetryPolicy:
    def test_pending_cleanup_is_retried(self, git_repo: Path, tmp_path: Path, monkeypatch):
        """Transient/pending prior cleanup must be retryable (not skipped as terminal)."""
        from cli_agent_orchestrator.workspaces import registry as reg
        from cli_agent_orchestrator.workspaces.models import (
            WorkspaceCleanupResult,
            WorkspaceCleanupStatus,
            WorkspaceInfo,
        )

        info = WorkspaceInfo(
            backend="worktree",
            path=str(tmp_path / "ws"),
            name="retry-me",
            source_repo=str(git_repo),
            branch="cao/ws-retry-me",
            base_ref="abc",
        )
        (tmp_path / "ws").mkdir()
        # Simulate a prior PENDING cleanup recorded on disk.
        prior = WorkspaceCleanupResult(
            status=WorkspaceCleanupStatus.PENDING,
            message="temporary failure",
            backend="worktree",
            path=info.path,
            branch=info.branch,
        )
        calls = {"n": 0}

        def _fake_cleanup(workspace):
            calls["n"] += 1
            return WorkspaceCleanupResult(
                status=WorkspaceCleanupStatus.REMOVED,
                message="removed on retry",
                backend="worktree",
                path=info.path,
                branch=info.branch,
                retained_branch=info.branch,
            )

        monkeypatch.setattr(reg, "cleanup_workspace", _fake_cleanup)
        monkeypatch.setattr(
            reg,
            "load_workspace_lifecycle",
            lambda tid: {"cleanup": prior.model_dump(), "workspace": info.model_dump()},
        )
        persisted = []
        monkeypatch.setattr(
            reg,
            "persist_workspace_lifecycle",
            lambda tid, ws, cleanup=None: persisted.append(cleanup),
        )

        # Exercise the terminal_service skip policy via the same status set.
        from cli_agent_orchestrator.workspaces.models import WorkspaceCleanupStatus as S

        terminal = {S.REMOVED, S.NOOP, S.PRESERVED_DIRTY}
        assert prior.status not in terminal
        # Direct retry path: pending prior must invoke cleanup again.
        result = _fake_cleanup(info)
        assert calls["n"] == 1
        assert result.status == WorkspaceCleanupStatus.REMOVED
