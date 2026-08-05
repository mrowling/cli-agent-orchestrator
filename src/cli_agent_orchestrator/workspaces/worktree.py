"""Git worktree workspace backend — portable isolation + merge substrate (D11).

Committed-ref constraint (D11): a worktree is created from ``HEAD``'s commit
(or another explicit commit-ish). Dirty uncommitted/index state in the source
checkout is **never** copied into the worker workspace. Supervisors with
uncommitted work must commit (or stash) before expecting workers to see it,
or use ``shared`` / (future) ``rift``.

Cleanup policy (Item 4 / D11):
- Clean worktree whose commits live on its retained branch → ``git worktree
  remove`` (no ``--force``); branch kept for manual merge.
- Dirty or untracked files → preserve the worktree; return
  ``preserved_dirty`` with actionable guidance. Never force-remove.
- Idempotent: missing path / already-pruned worktree → ``noop``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

from cli_agent_orchestrator.workspaces.base import (
    WorkspaceBackend,
    WorkspaceBackendError,
    WorkspaceNameCollisionError,
)
from cli_agent_orchestrator.workspaces.models import (
    WorkspaceCleanupResult,
    WorkspaceCleanupStatus,
    WorkspaceInfo,
    WorkspacePatch,
)

logger = logging.getLogger(__name__)

# Safe structural argv tokens for git invocations (shell=False). Rejects
# crafted --flags and control characters; paths use a slightly wider set.
_SAFE_TOKEN_RE = re.compile(r"^[\w./@~+-]+$")
_SAFE_PATH_RE = re.compile(r"^[\w./@~+ =,-]+$", re.UNICODE)
_BRANCH_RE = re.compile(r"^cao/ws-[a-zA-Z0-9._-]{1,120}$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")

_GIT_TIMEOUT = 60


def _validate_token(token: str, *, path: bool = False) -> str:
    pattern = _SAFE_PATH_RE if path else _SAFE_TOKEN_RE
    if not token or not pattern.fullmatch(token):
        raise WorkspaceBackendError(f"unsafe git argument rejected: {token!r}")
    if token.startswith("-"):
        raise WorkspaceBackendError(f"git flag injection rejected: {token!r}")
    return token


def _run_git(
    args: Sequence[str],
    *,
    cwd: Optional[str] = None,
    check: bool = True,
    timeout: int = _GIT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with argv list only (``shell=False``)."""
    argv = ["git", *[str(a) for a in args]]
    # Validate every non-flag token; allow known git flags we pass explicitly.
    for a in argv[1:]:
        if a.startswith("-"):
            continue
        _validate_token(a, path=True)
    try:
        completed = subprocess.run(  # noqa: S603 — argv validated; shell=False
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorkspaceBackendError("git binary not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceBackendError(f"git timed out: {' '.join(argv)}") from exc
    if check and completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise WorkspaceBackendError(f"git {' '.join(args)} failed: {err}")
    return completed


def _find_git_toplevel(path: str) -> str:
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise WorkspaceBackendError(f"not a directory: {real}")
    completed = _run_git(
        ["rev-parse", "--show-toplevel"],
        cwd=real,
        check=False,
    )
    if completed.returncode != 0:
        raise WorkspaceBackendError(
            f"path is not inside a git repository: {real}. "
            "workspace=worktree requires a git repo; use workspace=shared or "
            "workspace=auto (falls back to shared)."
        )
    return os.path.realpath(completed.stdout.strip())


class WorktreeWorkspaceBackend(WorkspaceBackend):
    """Branch-isolated git worktree per worker."""

    name = "worktree"

    def __init__(self, workspaces_root: Optional[str] = None) -> None:
        self._workspaces_root = workspaces_root

    def probe(self, from_path: str | None = None) -> bool:
        if from_path is None:
            from_path = os.getcwd()
        try:
            _find_git_toplevel(from_path)
            _run_git(["--version"], check=True)
            return True
        except WorkspaceBackendError:
            return False

    def create(self, from_path: str, name: str) -> WorkspaceInfo:
        if not _NAME_RE.fullmatch(name):
            raise WorkspaceBackendError(f"workspace name must match {_NAME_RE.pattern}: {name!r}")
        source_repo = _find_git_toplevel(from_path)

        # Committed-ref only — never copy dirty index/worktree state.
        base_ref = _run_git(["rev-parse", "HEAD"], cwd=source_repo).stdout.strip()
        _validate_token(base_ref)

        branch = f"cao/ws-{name}"
        if not _BRANCH_RE.fullmatch(branch):
            raise WorkspaceBackendError(f"invalid derived branch name: {branch!r}")

        root = Path(self._workspaces_root or _default_workspaces_root())
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        worktree_path = (root / name).resolve()
        if worktree_path.exists():
            raise WorkspaceNameCollisionError(
                name=name,
                branch=branch,
                path=str(worktree_path),
                detail="workspace path already exists; refusing to overwrite",
            )

        # Refuse to reuse an existing branch — branch is retained after clean
        # worktree removal for manual merge, so a repeated name would collide.
        branch_check = _run_git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=source_repo,
            check=False,
        )
        if branch_check.returncode == 0:
            raise WorkspaceNameCollisionError(
                name=name,
                branch=branch,
                path=str(worktree_path),
                detail=(
                    f"branch {branch} already exists (retained from a prior "
                    "worktree). Choose a unique workspace name; existing "
                    "branches are never overwritten."
                ),
            )

        # Create branch at base_ref and attach a new worktree.
        try:
            _run_git(
                ["worktree", "add", "-b", branch, str(worktree_path), base_ref],
                cwd=source_repo,
            )
        except WorkspaceBackendError as exc:
            err = str(exc).lower()
            if "already exists" in err or "is already used" in err:
                raise WorkspaceNameCollisionError(
                    name=name,
                    branch=branch,
                    path=str(worktree_path),
                    detail=str(exc),
                ) from exc
            raise
        logger.info(
            "Created worktree workspace name=%s path=%s branch=%s base_ref=%s "
            "(committed-ref only; dirty source state was not copied)",
            name,
            worktree_path,
            branch,
            base_ref[:12],
        )
        return WorkspaceInfo(
            backend=self.name,
            path=str(worktree_path),
            name=name,
            source_repo=source_repo,
            branch=branch,
            base_ref=base_ref,
            parent_path=source_repo,
        )

    def diff(self, workspace: WorkspaceInfo) -> WorkspacePatch:
        if not workspace.path or not os.path.isdir(workspace.path):
            return WorkspacePatch(
                unified_diff="",
                base_ref=workspace.base_ref,
                head_ref=None,
                paths=[],
            )
        base = workspace.base_ref or "HEAD"
        # Include commits + unstaged for arbiter input; still backend-independent text.
        completed = _run_git(
            ["diff", "--name-only", base],
            cwd=workspace.path,
            check=False,
        )
        paths = [p for p in completed.stdout.splitlines() if p.strip()]
        diff_completed = _run_git(
            ["diff", base],
            cwd=workspace.path,
            check=False,
        )
        head = _run_git(["rev-parse", "HEAD"], cwd=workspace.path, check=False)
        head_ref = head.stdout.strip() if head.returncode == 0 else None
        return WorkspacePatch(
            unified_diff=diff_completed.stdout or "",
            base_ref=workspace.base_ref,
            head_ref=head_ref,
            paths=paths,
        )

    def remove(self, workspace: WorkspaceInfo) -> WorkspaceCleanupResult:
        path = workspace.path
        source = workspace.source_repo
        branch = workspace.branch

        if not path or not os.path.isdir(path):
            return WorkspaceCleanupResult(
                status=WorkspaceCleanupStatus.NOOP,
                message=f"worktree path already absent (idempotent): {path}",
                backend=self.name,
                path=path,
                branch=branch,
                base_ref=workspace.base_ref,
                source_repo=source,
                retained_branch=branch,
            )

        # Dirty / untracked → preserve. Never --force.
        status = _run_git(["status", "--porcelain"], cwd=path, check=False)
        porcelain = (status.stdout or "").strip()
        patch = self.diff(workspace)
        diff_tip = (patch.unified_diff or "")[:2000] or None

        if porcelain:
            msg = (
                f"worktree has uncommitted/untracked changes; preserved at {path}. "
                f"Commit or stash inside the worktree, then: "
                f"git -C {source or path} worktree remove {path} "
                f"(branch {branch} retained for manual merge: "
                f"git -C {source or path} merge {branch}). "
                "Never use git worktree remove --force — it destroys unmerged work."
            )
            logger.warning("Workspace cleanup preserved dirty worktree: %s", path)
            return WorkspaceCleanupResult(
                status=WorkspaceCleanupStatus.PRESERVED_DIRTY,
                message=msg,
                backend=self.name,
                path=path,
                branch=branch,
                base_ref=workspace.base_ref,
                source_repo=source,
                retained_branch=branch,
                diff_summary=diff_tip,
            )

        if not source or not os.path.isdir(source):
            return WorkspaceCleanupResult(
                status=WorkspaceCleanupStatus.PENDING,
                message=(
                    f"cannot remove worktree {path}: source_repo missing "
                    f"({source!r}). Remove manually with git worktree remove "
                    f"after locating the source repo. Branch {branch} should be "
                    f"retained for: git merge {branch}"
                ),
                backend=self.name,
                path=path,
                branch=branch,
                base_ref=workspace.base_ref,
                source_repo=source,
                retained_branch=branch,
                diff_summary=diff_tip,
            )

        # Detach worktree without deleting the branch (no --force).
        try:
            _run_git(["worktree", "remove", path], cwd=source, check=True)
        except WorkspaceBackendError as exc:
            return WorkspaceCleanupResult(
                status=WorkspaceCleanupStatus.PENDING,
                message=(
                    f"git worktree remove failed for {path}: {exc}. "
                    f"Worktree preserved. Manual: git -C {source} worktree remove {path}; "
                    f"merge with: git -C {source} merge {branch}"
                ),
                backend=self.name,
                path=path,
                branch=branch,
                base_ref=workspace.base_ref,
                source_repo=source,
                retained_branch=branch,
                diff_summary=diff_tip,
            )

        msg = (
            f"worktree removed at {path}; branch {branch} retained at "
            f"base_ref={workspace.base_ref}. Manual merge: "
            f"git -C {source} merge {branch}"
        )
        logger.info(msg)
        return WorkspaceCleanupResult(
            status=WorkspaceCleanupStatus.REMOVED,
            message=msg,
            backend=self.name,
            path=path,
            branch=branch,
            base_ref=workspace.base_ref,
            source_repo=source,
            retained_branch=branch,
            diff_summary=diff_tip,
        )

    def ancestors(self, workspace: WorkspaceInfo) -> List[WorkspaceInfo]:
        if not workspace.parent_path and not workspace.source_repo:
            return []
        parent = workspace.parent_path or workspace.source_repo
        assert parent is not None
        return [
            WorkspaceInfo(
                backend="shared",
                path=parent,
                name="source",
                source_repo=parent,
                branch=None,
                base_ref=workspace.base_ref,
                parent_path=None,
            )
        ]


def _default_workspaces_root() -> str:
    from cli_agent_orchestrator.constants import WORKSPACES_DIR

    return str(WORKSPACES_DIR)
