"""Pluggable workspace backends (swarm-economics T1.4 / D11–D13).

``shared`` (default) | ``worktree`` | ``rift`` (deferred) | ``auto``.
Merge arbitration (D13) is out of scope — see ``docs/workspace-backends.md``.
"""

from cli_agent_orchestrator.workspaces.base import (
    WorkspaceBackend,
    WorkspaceBackendError,
    WorkspaceNameCollisionError,
    WorkspaceNotAvailableError,
)
from cli_agent_orchestrator.workspaces.factory import (
    create_workspace_for_terminal,
    resolve_workspace_selection,
    select_backend,
)
from cli_agent_orchestrator.workspaces.models import (
    WorkspaceCleanupResult,
    WorkspaceCleanupStatus,
    WorkspaceInfo,
    WorkspacePatch,
)
from cli_agent_orchestrator.workspaces.registry import (
    cleanup_workspace,
    load_workspace_lifecycle,
    persist_workspace_lifecycle,
)

__all__ = [
    "WorkspaceBackend",
    "WorkspaceBackendError",
    "WorkspaceNameCollisionError",
    "WorkspaceNotAvailableError",
    "WorkspaceInfo",
    "WorkspacePatch",
    "WorkspaceCleanupResult",
    "WorkspaceCleanupStatus",
    "resolve_workspace_selection",
    "select_backend",
    "create_workspace_for_terminal",
    "cleanup_workspace",
    "persist_workspace_lifecycle",
    "load_workspace_lifecycle",
]
