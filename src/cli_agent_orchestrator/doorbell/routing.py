"""Resolve the king terminal id for doorbell delivery."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

_TERMINAL_ID_RE = re.compile(r"^[a-f0-9]{8}$")
_DEFAULT_STATE_REL = Path(".swarm/state.json")


class DoorbellRoutingError(ValueError):
    """Raised when king terminal routing cannot be resolved."""


def _validate_terminal_id(value: str, *, field_name: str) -> str:
    terminal_id = value.strip()
    if not _TERMINAL_ID_RE.fullmatch(terminal_id):
        raise DoorbellRoutingError(
            f"{field_name} must be an 8-character lowercase hex terminal id " f"(got {value!r})"
        )
    return terminal_id


def _load_state_json(state_path: Path) -> dict[str, Any]:
    if not state_path.is_file():
        raise DoorbellRoutingError(
            f"swarm state file not found: {state_path} — pass --to <terminal_id> "
            "or run from a repo with .swarm/state.json"
        )
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DoorbellRoutingError(
            f"invalid JSON in {state_path}: {exc} — fix the file or pass --to explicitly"
        ) from exc
    if not isinstance(payload, dict):
        raise DoorbellRoutingError(
            f"{state_path} must contain a JSON object — got {type(payload).__name__}"
        )
    return payload


def king_terminal_id_from_state(
    state_path: Path,
) -> str:
    """Read ``king_terminal_id`` from thin (or fat-tolerant) ``state.json``."""

    payload = _load_state_json(state_path)
    # Fat/legacy keys are tolerated; only king_terminal_id is required here.
    raw = payload.get("king_terminal_id")
    if raw is None:
        raise DoorbellRoutingError(
            f"{state_path} is missing king_terminal_id — set it after launching the king "
            "or pass --to <terminal_id>"
        )
    if not isinstance(raw, str):
        raise DoorbellRoutingError(
            f"{state_path} king_terminal_id must be a string — got {type(raw).__name__}"
        )
    if not raw.strip():
        raise DoorbellRoutingError(
            f"{state_path} king_terminal_id is empty — set it to the king terminal id "
            "or pass --to <terminal_id>"
        )
    return _validate_terminal_id(raw, field_name="king_terminal_id")


def resolve_king_terminal_id(
    *,
    explicit_to: Optional[str] = None,
    cwd: Optional[Path] = None,
    state_path: Optional[Path] = None,
) -> str:
    """Resolve the delivery target terminal id.

    Explicit ``--to`` wins. Otherwise read ``king_terminal_id`` from
    ``.swarm/state.json`` relative to ``cwd`` (default: process cwd).
    """

    if explicit_to:
        return _validate_terminal_id(explicit_to, field_name="--to")

    base = cwd or Path.cwd()
    resolved_state = state_path or (base / _DEFAULT_STATE_REL)
    return king_terminal_id_from_state(resolved_state)
