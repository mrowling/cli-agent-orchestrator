"""Parse worker completion sentinels from captured CAO output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

DoneStatus = Literal["ok", "fail", "blocked"]

# Sentinel must occupy its own line; summary is the remainder of that line.
_DONE_SENTINEL_RE = re.compile(r"^===CAO_DONE=== status=(ok|fail|blocked) summary=(.+)$")


@dataclass(frozen=True)
class ParsedDoneSentinel:
    """A valid ``===CAO_DONE===`` line parsed from worker output."""

    status: DoneStatus
    summary: str


def parse_done_sentinel(captured_output: Optional[str]) -> Optional[ParsedDoneSentinel]:
    """Return the final valid done sentinel in ``captured_output``, if any.

    Contract:
    - Scan every line in order.
    - A line counts only when it fully matches
      ``===CAO_DONE=== status=ok|fail|blocked summary=<one line>``.
    - Return the **last** matching line (final-valid wins).
    - Malformed trailing sentinel-like noise does **not** erase an earlier
      valid sentinel — non-matching ``===CAO_DONE===`` lines are ignored.
    - Missing or empty capture returns ``None`` (backward compatible).
    """

    if not captured_output:
        return None

    last_valid: Optional[ParsedDoneSentinel] = None
    for line in captured_output.splitlines():
        candidate = line.strip()
        if not candidate.startswith("===CAO_DONE==="):
            continue
        match = _DONE_SENTINEL_RE.match(candidate)
        if match is None:
            continue
        last_valid = ParsedDoneSentinel(
            status=match.group(1),  # type: ignore[arg-type]
            summary=match.group(2).strip(),
        )
    return last_valid
