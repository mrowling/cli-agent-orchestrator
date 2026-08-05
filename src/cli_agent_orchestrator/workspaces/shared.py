"""Shared workspace backend — current cwd behaviour (D11 default)."""

from __future__ import annotations

import os
from typing import List

from cli_agent_orchestrator.workspaces.base import WorkspaceBackend
from cli_agent_orchestrator.workspaces.models import (
    WorkspaceCleanupResult,
    WorkspaceCleanupStatus,
    WorkspaceInfo,
    WorkspacePatch,
)


class SharedWorkspaceBackend(WorkspaceBackend):
    """Inherit the supervisor/source path — no isolation.

    Remains the shipped default (D11): isolation changes where agents write,
    which is a large behavioural change deserving an opt-in period.
    """

    name = "shared"

    def probe(self, from_path: str | None = None) -> bool:
        return True

    def create(self, from_path: str, name: str) -> WorkspaceInfo:
        path = os.path.realpath(from_path) if from_path else os.path.realpath(os.getcwd())
        return WorkspaceInfo(
            backend=self.name,
            path=path,
            name=name,
            source_repo=None,
            branch=None,
            base_ref=None,
            parent_path=None,
        )

    def diff(self, workspace: WorkspaceInfo) -> WorkspacePatch:
        # No isolation — empty patch. Shared workers write into the source tree.
        return WorkspacePatch(unified_diff="", base_ref=None, head_ref=None, paths=[])

    def remove(self, workspace: WorkspaceInfo) -> WorkspaceCleanupResult:
        return WorkspaceCleanupResult(
            status=WorkspaceCleanupStatus.NOOP,
            message="shared workspace has no isolated path to remove",
            backend=self.name,
            path=workspace.path,
        )

    def ancestors(self, workspace: WorkspaceInfo) -> List[WorkspaceInfo]:
        return []
