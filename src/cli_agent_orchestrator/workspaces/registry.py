"""Workspace backend registry + durable lifecycle metadata (survives delete).

Terminal DB rows are deleted on ``delete_terminal``. Workspace metadata and
cleanup outcomes are written under ``TERMINAL_LOG_DIR`` so handoff results and
operators can still retrieve backend/path/branch/base/diff/cleanup after
auto-cleanup (Item 4).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, cast

from cli_agent_orchestrator.workspaces.base import WorkspaceBackend
from cli_agent_orchestrator.workspaces.factory import get_backend
from cli_agent_orchestrator.workspaces.models import (
    WorkspaceCleanupResult,
    WorkspaceInfo,
)

logger = logging.getLogger(__name__)

_backend: Optional[WorkspaceBackend] = None


def get_workspace_backend(name: str = "shared") -> WorkspaceBackend:
    """Return a backend instance by concrete name (not auto)."""
    return get_backend(name)


def set_workspace_backend(backend: WorkspaceBackend) -> None:
    """Test seam: pin a module-level backend."""
    global _backend
    _backend = backend


def workspace_lifecycle_path(terminal_id: str) -> Path:
    from cli_agent_orchestrator.constants import TERMINAL_LOG_DIR

    return TERMINAL_LOG_DIR / f"{terminal_id}.workspace.json"


def persist_workspace_lifecycle(
    terminal_id: str,
    workspace: Optional[WorkspaceInfo],
    cleanup: Optional[WorkspaceCleanupResult] = None,
) -> None:
    """Write workspace meta (+ optional cleanup) so it survives DB delete."""
    if workspace is None and cleanup is None:
        return
    payload: Dict[str, Any] = {"terminal_id": terminal_id}
    if workspace is not None:
        payload["workspace"] = workspace.model_dump()
    if cleanup is not None:
        payload["cleanup"] = cleanup.model_dump()
    path = workspace_lifecycle_path(terminal_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to persist workspace lifecycle for %s: %s", terminal_id, exc)


def load_workspace_lifecycle(terminal_id: str) -> Optional[Dict[str, Any]]:
    path = workspace_lifecycle_path(terminal_id)
    if not path.is_file():
        return None
    try:
        return cast(Dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load workspace lifecycle for %s: %s", terminal_id, exc)
        return None


def cleanup_workspace(
    workspace: Optional[WorkspaceInfo],
) -> Optional[WorkspaceCleanupResult]:
    """Run backend.remove for a recorded workspace; idempotent."""
    if workspace is None:
        return None
    backend = get_backend(workspace.backend)
    return backend.remove(workspace)
