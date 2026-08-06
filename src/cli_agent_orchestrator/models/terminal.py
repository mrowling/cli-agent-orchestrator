from datetime import datetime
from enum import Enum
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from cli_agent_orchestrator.models.provider import ProviderType

# Terminal ID validation (8 character hex string)
TerminalId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{8}$")]


class TerminalStatus(str, Enum):
    """Terminal status enumeration with provider-aware states."""

    UNKNOWN = "unknown"
    IDLE = "idle"
    PROCESSING = "processing"
    COMPLETED = "completed"
    WAITING_USER_ANSWER = "waiting_user_answer"
    ERROR = "error"


class Terminal(BaseModel):
    """Terminal model - represents a tmux window."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(..., description="Unique terminal identifier")
    name: str = Field(..., description="Terminal/window name")
    provider: ProviderType = Field(..., description="CLI tool provider")
    session_name: str = Field(..., description="Session name")
    agent_profile: Optional[str] = Field(None, description="Agent profile")
    caller_id: Optional[str] = Field(
        None, description="Terminal that created this one via handoff/assign (callback target)"
    )
    allowed_tools: Optional[List[str]] = Field(None, description="Allowed CAO tools")
    shell_command: Optional[str] = Field(
        None, description="Shell process name captured before kiro launch"
    )
    # D11 workspace isolation (nullable — shared default / pre-feature terminals).
    workspace_backend: Optional[str] = Field(
        None, description="Workspace backend used for this terminal (shared|worktree|rift)"
    )
    workspace_path: Optional[str] = Field(
        None, description="Absolute workspace path when isolated (worker cwd)"
    )
    workspace_branch: Optional[str] = Field(
        None, description="Isolated branch name when backend=worktree"
    )
    workspace_base_ref: Optional[str] = Field(
        None, description="Committed git ref the workspace was created from"
    )
    status: Optional[TerminalStatus] = Field(
        None, description="Current terminal status (live only)"
    )
    last_active: Optional[datetime] = Field(None, description="Last active timestamp")


class ContextUsageResponse(BaseModel):
    """Provider context-window usage scraped from the terminal TUI footer.

    Observe-only: CAO does not autocompact. ``ratio`` is ``None`` when the
    provider cannot report usage (unsupported adapter or footer not visible).
    """

    terminal_id: str = Field(..., description="Terminal identifier")
    ratio: Optional[float] = Field(
        None,
        description="Context window fraction used (0.0–1.0), or null if unknown",
        ge=0.0,
        le=1.0,
    )
    source: str = Field(
        ...,
        description='Where the ratio came from: "screen" when parsed from the TUI, else "unknown"',
    )
    provider: str = Field(..., description="Provider type for this terminal")


class AgentStepResult(BaseModel):
    """Transient result of one agent step (issue #312, C3b). Not persisted.

    ``run_agent_step`` returns this ONLY on success (status COMPLETED); all
    failure modes raise narrow exceptions instead. It lives here in the terminal
    layer (not the workflow module) because it is the generic step substrate's
    return type and is conceptually workflow-independent — keeping it out of
    ``models/workflow.py`` lets ``services/agent_step.py`` avoid importing the
    workflow module (and its jsonschema/yaml deps).
    """

    terminal_id: str
    last_message: str
    status: TerminalStatus
    # D11: populated after teardown so handoff can report workspace/cleanup
    # without depending on a live DB row.
    workspace_backend: Optional[str] = None
    workspace_path: Optional[str] = None
    workspace_branch: Optional[str] = None
    workspace_base_ref: Optional[str] = None
    workspace_diff: Optional[str] = None
    workspace_cleanup_status: Optional[str] = None
    workspace_cleanup_message: Optional[str] = None
    workspace_retained_branch: Optional[str] = None
