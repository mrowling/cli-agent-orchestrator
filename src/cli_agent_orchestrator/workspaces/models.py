"""Typed metadata and result objects for the workspace backend family (D11).

Aligned with ``docs/issues/swarm-economics/design.md`` T1.4 / D11–D13.
D13 merge arbiter is out of scope — ``diff()`` exists so an arbiter *could*
consume a backend-independent patch later; this package does not arbitrate.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkspaceBackendName(str, Enum):
    """Selectable workspace backends (plus ``auto`` for probe-based selection)."""

    SHARED = "shared"
    WORKTREE = "worktree"
    RIFT = "rift"
    AUTO = "auto"


VALID_WORKSPACE_BACKENDS = frozenset(m.value for m in WorkspaceBackendName)


class WorkspaceCleanupStatus(str, Enum):
    """Outcome of ``WorkspaceBackend.remove`` / terminal delete cleanup."""

    REMOVED = "removed"
    """Worktree detached/removed; branch/ref retained for manual merge."""

    PRESERVED_DIRTY = "preserved_dirty"
    """Uncommitted/untracked work present — worktree left intact."""

    NOOP = "noop"
    """Nothing to remove (shared backend, already cleaned, or missing path)."""

    PENDING = "pending"
    """Cleanup needed but could not complete; actionable message attached."""


class WorkspaceInfo(BaseModel):
    """Recorded workspace identity — D11 ``WorkspacePath`` plus merge metadata.

    Persisted on the terminal row so create → use → delete → handoff-result
    can all recover backend/path/branch/base without depending on a live pane.
    """

    backend: str = Field(description="Concrete backend that created this workspace")
    path: str = Field(description="Absolute workspace path (worker cwd)")
    name: str = Field(description="Unique workspace name used at create time")
    source_repo: Optional[str] = Field(
        None, description="Absolute path of the source git repository (worktree)"
    )
    branch: Optional[str] = Field(
        None, description="Isolated branch name (worktree); None for shared"
    )
    base_ref: Optional[str] = Field(
        None,
        description=(
            "Committed git ref the workspace was created from. Worktree never "
            "silently copies dirty uncommitted state (D11 committed-ref constraint)."
        ),
    )
    parent_path: Optional[str] = Field(
        None, description="Parent workspace/source path for ancestry (D11)"
    )

    def to_public_dict(self) -> Dict[str, Any]:
        """Nullable-friendly dict for assign receipts / HandoffResult."""
        return {
            "workspace_backend": self.backend,
            "workspace_path": self.path,
            "workspace_name": self.name,
            "workspace_source_repo": self.source_repo,
            "workspace_branch": self.branch,
            "workspace_base_ref": self.base_ref,
        }


class WorkspacePatch(BaseModel):
    """Backend-independent patch for a future D13 merge arbiter."""

    unified_diff: str = Field(default="", description="Unified diff vs base_ref / parent")
    base_ref: Optional[str] = None
    head_ref: Optional[str] = None
    paths: List[str] = Field(default_factory=list)


class WorkspaceCleanupResult(BaseModel):
    """Result of attempting to remove a workspace after terminal delete/handoff."""

    status: WorkspaceCleanupStatus
    message: str
    backend: Optional[str] = None
    path: Optional[str] = None
    branch: Optional[str] = None
    base_ref: Optional[str] = None
    source_repo: Optional[str] = None
    retained_branch: Optional[str] = Field(
        default=None,
        description="Branch still present after worktree removal (manual merge target)",
    )
    diff_summary: Optional[str] = Field(
        default=None,
        description="Short diff tip when cleanup preserves or removes a worktree",
    )

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "workspace_cleanup_status": (
                self.status.value
                if isinstance(self.status, WorkspaceCleanupStatus)
                else self.status
            ),
            "workspace_cleanup_message": self.message,
            "workspace_backend": self.backend,
            "workspace_path": self.path,
            "workspace_branch": self.branch or self.retained_branch,
            "workspace_base_ref": self.base_ref,
            "workspace_source_repo": self.source_repo,
            "workspace_retained_branch": self.retained_branch,
            "workspace_diff": self.diff_summary,
        }
