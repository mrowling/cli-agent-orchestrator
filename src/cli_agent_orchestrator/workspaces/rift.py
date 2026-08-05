"""Rift workspace backend — deferred until D11 asks (1) and (4) land.

``probe()`` always returns False so ``auto`` skips rift. Explicit
``workspace=rift`` raises an actionable ``WorkspaceNotAvailableError``.
"""

from __future__ import annotations

from typing import List

from cli_agent_orchestrator.workspaces.base import (
    WorkspaceBackend,
    WorkspaceNotAvailableError,
)
from cli_agent_orchestrator.workspaces.models import (
    WorkspaceCleanupResult,
    WorkspaceInfo,
    WorkspacePatch,
)

_DEFERRED_MSG = (
    "workspace backend 'rift' is reserved/deferred (swarm-economics D11). "
    "Use workspace=worktree for isolation, workspace=shared for current cwd "
    "behaviour, or workspace=auto to probe worktree then fall back to shared. "
    "Rift will be enabled once CoW clone + parent-aware diff are available."
)


class RiftWorkspaceBackend(WorkspaceBackend):
    """Placeholder — not implemented; probe fails so auto skips it."""

    name = "rift"

    def probe(self, from_path: str | None = None) -> bool:
        return False

    def create(self, from_path: str, name: str) -> WorkspaceInfo:
        raise WorkspaceNotAvailableError(_DEFERRED_MSG)

    def diff(self, workspace: WorkspaceInfo) -> WorkspacePatch:
        raise WorkspaceNotAvailableError(_DEFERRED_MSG)

    def remove(self, workspace: WorkspaceInfo) -> WorkspaceCleanupResult:
        raise WorkspaceNotAvailableError(_DEFERRED_MSG)

    def ancestors(self, workspace: WorkspaceInfo) -> List[WorkspaceInfo]:
        raise WorkspaceNotAvailableError(_DEFERRED_MSG)
