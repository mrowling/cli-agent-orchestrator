"""Run optional handoff ``done_cmd`` mechanical acceptance checks (ADT-3)."""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

from cli_agent_orchestrator.constants import (
    DONE_CMD_OUTPUT_MAX_CHARS,
    DONE_CMD_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DoneCmdVerification:
    """Outcome of executing a manager-provided ``done_cmd`` verifier."""

    done_cmd: str
    exit_code: Optional[int] = None
    output: Optional[str] = None
    timed_out: bool = False
    error: Optional[str] = None

    @property
    def accepted(self) -> bool:
        """True only when the command exited 0 with no timeout or spawn/parse error."""

        return (
            self.error is None
            and not self.timed_out
            and self.exit_code is not None
            and self.exit_code == 0
        )


def _bound_output(text: str, *, max_chars: int = DONE_CMD_OUTPUT_MAX_CHARS) -> str:
    """Return ``text``, tail-truncated when it exceeds ``max_chars``."""

    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return (
        f"[... output truncated: omitted first {omitted} chars, "
        f"showing last {max_chars} ...]\n{text[-max_chars:]}"
    )


def _decode_captured(raw: Optional[object]) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate the spawned process group/tree (POSIX) or process (fallback)."""

    if proc.poll() is not None:
        return
    pid = proc.pid
    try:
        if sys.platform != "win32" and hasattr(os, "killpg"):
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError:
                proc.terminate()
        else:
            proc.terminate()
    except ProcessLookupError:
        return

    try:
        proc.wait(timeout=2.0)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        if sys.platform != "win32" and hasattr(os, "killpg"):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError:
                proc.kill()
        else:
            proc.kill()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        logger.warning("done_cmd process %s did not exit after SIGKILL", pid)


def run_done_cmd(done_cmd: str, *, cwd: Optional[str] = None) -> DoneCmdVerification:
    """Parse and execute ``done_cmd`` with ``shell=False``.

    ``done_cmd`` is a trusted manager-provided string tokenized via
    ``shlex.split`` — never interpolated into a shell. Empty argv, spawn
    failures, non-zero exit, and timeouts are returned as structured audit
    fields rather than raised.

    On timeout the entire process group/tree is terminated on POSIX (the child
    is started in a new session via ``start_new_session=True``). Other platforms
    fall back to terminating the immediate process. Output remains bounded.
    """

    command = done_cmd.strip()
    logger.info("handoff done_cmd verifier: %s", done_cmd)

    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return DoneCmdVerification(
            done_cmd=done_cmd,
            error=f"done_cmd parse error: {exc}",
        )

    if not argv:
        return DoneCmdVerification(
            done_cmd=done_cmd,
            error="done_cmd produced empty argv after parsing",
        )

    popen_kwargs: dict = {
        "cwd": cwd,
        "shell": False,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    # POSIX: new session ⇒ process group == pid, so timeout can kill the tree.
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603 — argv from shlex; shell=False
    except OSError as exc:
        return DoneCmdVerification(
            done_cmd=done_cmd,
            error=f"done_cmd spawn error: {exc}",
        )

    try:
        stdout, stderr = proc.communicate(timeout=DONE_CMD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        combined = _decode_captured(stdout) + _decode_captured(stderr)
        return DoneCmdVerification(
            done_cmd=done_cmd,
            output=_bound_output(combined) if combined else None,
            timed_out=True,
            error=f"done_cmd timed out after {DONE_CMD_TIMEOUT_SECONDS}s",
        )

    combined = (stdout or "") + (stderr or "")
    return DoneCmdVerification(
        done_cmd=done_cmd,
        exit_code=proc.returncode,
        output=_bound_output(combined) if combined else None,
    )
