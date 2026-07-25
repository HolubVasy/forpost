"""Command execution via subprocess."""

import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Result of a command execution."""

    stdout: str
    stderr: str
    exit_code: int


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
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired as e:
        return CommandResult(
            stdout=e.stdout or "" if isinstance(e.stdout, str) else (e.stdout or b"").decode(),
            stderr=e.stderr or "" if isinstance(e.stderr, str) else (e.stderr or b"").decode(),
            exit_code=-1,
        )
