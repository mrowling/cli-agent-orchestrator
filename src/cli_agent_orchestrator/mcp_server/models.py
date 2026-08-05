"""MCP server models."""

from typing import Optional

from pydantic import BaseModel, Field


class HandoffResult(BaseModel):
    """Result of a handoff operation."""

    success: bool = Field(description="Whether the handoff was successful")
    message: str = Field(description="A message describing the result of the handoff")
    output: Optional[str] = Field(default=None, description="The output from the target agent")
    terminal_id: Optional[str] = Field(
        default=None, description="The terminal ID used for the handoff"
    )
    # D2: wall-clock duration of the blocking handoff (ms), when measurable.
    duration_ms: Optional[int] = Field(
        default=None, description="Wall-clock duration of the handoff in milliseconds"
    )
    # ADT-1: parsed from worker output when present; null preserves back-compat.
    done_status: Optional[str] = Field(
        default=None,
        description=(
            "Worker completion status parsed from ===CAO_DONE=== sentinel "
            "(ok, fail, or blocked) when present in captured output"
        ),
    )
    done_summary: Optional[str] = Field(
        default=None,
        description=("One-line worker summary parsed from ===CAO_DONE=== sentinel when present"),
    )
    # ADT-3: optional mechanical verifier audit fields; null when done_cmd omitted.
    done_cmd: Optional[str] = Field(
        default=None,
        description="Manager-provided done_cmd verifier string when handoff used one",
    )
    done_cmd_exit: Optional[int] = Field(
        default=None,
        description="Exit code from done_cmd when executed; null on parse/spawn failure",
    )
    done_cmd_output: Optional[str] = Field(
        default=None,
        description=(
            "Combined stdout+stderr from done_cmd, tail-truncated to "
            "DONE_CMD_OUTPUT_MAX_CHARS when long"
        ),
    )
    done_cmd_timed_out: Optional[bool] = Field(
        default=None,
        description="True when done_cmd exceeded DONE_CMD_TIMEOUT_SECONDS",
    )
    done_cmd_error: Optional[str] = Field(
        default=None,
        description="Parse, spawn, or timeout error from done_cmd when not accepted",
    )
    # D11 workspace isolation (nullable — preserves back-compat when shared/default).
    workspace_backend: Optional[str] = Field(
        default=None, description="Workspace backend used for the worker (shared|worktree|rift)"
    )
    workspace_path: Optional[str] = Field(
        default=None, description="Absolute worker workspace path when isolated"
    )
    workspace_branch: Optional[str] = Field(
        default=None, description="Isolated branch name when backend=worktree"
    )
    workspace_base_ref: Optional[str] = Field(
        default=None, description="Committed git ref the workspace was created from"
    )
    workspace_diff: Optional[str] = Field(
        default=None, description="Short unified diff tip vs base_ref after cleanup"
    )
    workspace_cleanup_status: Optional[str] = Field(
        default=None,
        description="Cleanup outcome: removed|preserved_dirty|noop|pending",
    )
    workspace_cleanup_message: Optional[str] = Field(
        default=None, description="Actionable cleanup / manual-merge guidance"
    )
    workspace_retained_branch: Optional[str] = Field(
        default=None, description="Branch retained after worktree removal for manual merge"
    )
