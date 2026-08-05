"""WorkspaceBackend ABC — D11 contract mirroring terminal backends.

See ``docs/issues/swarm-economics/design.md`` T1.4 / D11:

    WorkspaceBackend (ABC)
      probe()    -> bool
      create(from_path, name) -> WorkspaceInfo
      diff(workspace)         -> WorkspacePatch
      remove(workspace)       -> WorkspaceCleanupResult
      ancestors(workspace)    -> list[WorkspaceInfo]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from cli_agent_orchestrator.workspaces.models import (
    WorkspaceCleanupResult,
    WorkspaceInfo,
    WorkspacePatch,
)


class WorkspaceBackendError(Exception):
    """Base exception for workspace backend operations."""

    pass


class WorkspaceNotAvailableError(WorkspaceBackendError):
    """Raised when a backend is selected but not usable here (probe failed / deferred)."""

    pass


class WorkspaceNameCollisionError(WorkspaceBackendError):
    """Raised when a worktree path or branch already exists and cannot be reused safely.

    Callers should retry with a new unique name (factory does this) or surface
    the collision to the operator. Existing branches/paths are never overwritten.
    """

    def __init__(self, *, name: str, branch: str, path: str, detail: str) -> None:
        self.name = name
        self.branch = branch
        self.path = path
        super().__init__(
            f"workspace name collision for {name!r} (branch={branch}, path={path}): {detail}"
        )


class WorkspaceBackend(ABC):
    """Abstract workspace isolation backend (shared | worktree | rift)."""

    name: str = "workspace"

    @abstractmethod
    def probe(self, from_path: str | None = None) -> bool:
        """Return True when this backend is usable for ``from_path`` (or generally)."""
        ...

    @abstractmethod
    def create(self, from_path: str, name: str) -> WorkspaceInfo:
        """Create an isolated workspace derived from ``from_path``.

        ``name`` must be unique among live workspaces. Worktree creates from a
        *committed* ref only — dirty uncommitted state is never copied.
        """
        ...

    @abstractmethod
    def diff(self, workspace: WorkspaceInfo) -> WorkspacePatch:
        """Return a backend-independent patch vs the recorded base/parent (D13 input)."""
        ...

    @abstractmethod
    def remove(self, workspace: WorkspaceInfo) -> WorkspaceCleanupResult:
        """Remove the workspace when safe; never silently destroy unmerged work.

        Clean worktree whose commits live on its retained branch may be detached
        while preserving the branch. Dirty/untracked → preserve + actionable
        cleanup-pending result. Must be idempotent.
        """
        ...

    @abstractmethod
    def ancestors(self, workspace: WorkspaceInfo) -> List[WorkspaceInfo]:
        """Return ancestry chain (parent source first). Empty for shared roots."""
        ...
