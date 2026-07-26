"""Command execution via subprocess."""

import os
import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Result of a command execution."""

    stdout: str
    stderr: str
    exit_code: int


def _cap(text: str) -> str:
    """Truncate output to MAX_OUTPUT_CHARS, keeping the tail (where a command's
    conclusion / final error usually is). Protects the response, the model's
    context, and the on-disk log from runaway output.

    Note: this caps what we RETURN and LOG; the transient in-memory buffer from
    capture_output is not itself bounded here (that needs streaming reads - left
    for the async-job phase).
    """
    limit = int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))
    if limit and text is not None and len(text) > limit:
        omitted = len(text) - limit
        return f"[... {omitted} earlier chars truncated ...]\n" + text[-limit:]
    return text or ""


def execute(command: str, timeout: int | None = None) -> CommandResult:
    """
    Execute a shell command and capture output.

    Args:
        command: The shell command to execute.
        timeout: Optional timeout in seconds. None means no timeout.

    Returns:
        CommandResult with stdout, stderr, and exit_code.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(
            stdout=_cap(result.stdout),
            stderr=_cap(result.stderr),
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired as e:
        return CommandResult(
            stdout=_cap(e.stdout or "" if isinstance(e.stdout, str) else (e.stdout or b"").decode()),
            stderr=_cap(e.stderr or "" if isinstance(e.stderr, str) else (e.stderr or b"").decode()),
            exit_code=-1,
        )
