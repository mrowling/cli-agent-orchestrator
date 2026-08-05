"""Workspace backend factory — selection precedence and ``auto`` probing (D11).

Selection precedence (highest wins):

1. Explicit ``workspace=`` argument on assign / handoff / HTTP create / run-step
2. ``CAO_WORKSPACE_BACKEND`` environment variable
3. Built-in default: ``shared``

``auto`` probes in D11 spirit: rift (only if a real backend exists and
``probe()`` succeeds) → worktree → loud shared fallback. The shipped rift
implementation is deferred (``probe()`` always False), so ``auto`` effectively
tries worktree then shared until rift lands.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Optional, Tuple

from cli_agent_orchestrator.workspaces.base import (
    WorkspaceBackend,
    WorkspaceBackendError,
    WorkspaceNameCollisionError,
    WorkspaceNotAvailableError,
)
from cli_agent_orchestrator.workspaces.models import (
    VALID_WORKSPACE_BACKENDS,
    WorkspaceBackendName,
    WorkspaceInfo,
)
from cli_agent_orchestrator.workspaces.rift import RiftWorkspaceBackend
from cli_agent_orchestrator.workspaces.shared import SharedWorkspaceBackend
from cli_agent_orchestrator.workspaces.worktree import WorktreeWorkspaceBackend

logger = logging.getLogger(__name__)

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def resolve_workspace_selection(explicit: Optional[str] = None) -> str:
    """Resolve backend name from explicit arg → env → default ``shared``."""
    if explicit is not None and str(explicit).strip():
        name = str(explicit).strip().lower()
    else:
        name = os.environ.get("CAO_WORKSPACE_BACKEND", "shared").strip().lower() or "shared"
    if name not in VALID_WORKSPACE_BACKENDS:
        raise WorkspaceBackendError(
            f"Unknown workspace backend {name!r}. " f"Valid: {sorted(VALID_WORKSPACE_BACKENDS)}"
        )
    return name


def get_backend(name: str) -> WorkspaceBackend:
    """Return a concrete backend instance for a resolved (non-auto) name."""
    if name == WorkspaceBackendName.SHARED.value:
        return SharedWorkspaceBackend()
    if name == WorkspaceBackendName.WORKTREE.value:
        return WorktreeWorkspaceBackend()
    if name == WorkspaceBackendName.RIFT.value:
        return RiftWorkspaceBackend()
    raise WorkspaceBackendError(f"Cannot instantiate backend {name!r}")


def select_backend(
    explicit: Optional[str] = None,
    *,
    from_path: Optional[str] = None,
) -> Tuple[WorkspaceBackend, str]:
    """Select and instantiate a backend.

    Returns ``(backend, resolved_name)`` where ``resolved_name`` is the
    concrete backend actually chosen (never ``auto``).
    """
    selection = resolve_workspace_selection(explicit)
    base = from_path or os.getcwd()

    if selection == WorkspaceBackendName.AUTO.value:
        rift = RiftWorkspaceBackend()
        if rift.probe(base):
            logger.info("CAO workspace auto: selected rift (probe ok)")
            return rift, WorkspaceBackendName.RIFT.value
        worktree = WorktreeWorkspaceBackend()
        if worktree.probe(base):
            logger.info("CAO workspace auto: selected worktree (probe ok)")
            return worktree, WorkspaceBackendName.WORKTREE.value
        logger.warning(
            "CAO workspace auto: falling back to shared — rift unavailable/deferred "
            "and worktree probe failed for %s (not a git repo or git missing). "
            "Concurrent workers will share this cwd (D11 loud fallback).",
            base,
        )
        return SharedWorkspaceBackend(), WorkspaceBackendName.SHARED.value

    backend = get_backend(selection)
    if selection == WorkspaceBackendName.RIFT.value and not backend.probe(base):
        raise WorkspaceNotAvailableError(
            "workspace=rift is reserved/deferred (D11). " "Use worktree, shared, or auto."
        )
    if selection == WorkspaceBackendName.WORKTREE.value and not backend.probe(base):
        raise WorkspaceBackendError(
            f"workspace=worktree requested but probe failed for {base}. "
            "Path must be inside a git repository with git on PATH."
        )
    return backend, selection


def make_workspace_name(terminal_id: Optional[str] = None) -> str:
    """Deterministic-enough unique name for branch/path isolation."""
    tid = (terminal_id or uuid.uuid4().hex[:8]).lower()
    tid = _NAME_SAFE.sub("-", tid)[:32]
    suffix = uuid.uuid4().hex[:8]
    return f"{tid}-{suffix}"


def create_workspace_for_terminal(
    *,
    from_path: str,
    terminal_id: str,
    workspace: Optional[str] = None,
) -> WorkspaceInfo:
    """Factory helper used by terminal_service.create_terminal.

    Retries with a fresh unique name on ``WorkspaceNameCollisionError`` so
    retained branches from prior clean cleanups cannot block new workers.
    """
    backend, resolved = select_backend(workspace, from_path=from_path)
    last_exc: Optional[Exception] = None
    info: Optional[WorkspaceInfo] = None
    for attempt in range(5):
        name = make_workspace_name(terminal_id if attempt == 0 else None)
        try:
            info = backend.create(from_path, name)
            break
        except WorkspaceNameCollisionError as exc:
            last_exc = exc
            logger.warning(
                "Workspace name collision on attempt %s (%s); retrying with new name",
                attempt + 1,
                exc,
            )
            continue
    else:
        assert last_exc is not None
        raise last_exc
    assert info is not None
    # Ensure recorded backend matches what was selected (shared create already sets it).
    if info.backend != resolved:
        info = info.model_copy(update={"backend": resolved})
    return info
