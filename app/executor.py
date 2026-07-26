"""Command execution via subprocess, with bounded output and timeout.

Output is drained on background threads into a fixed-size ring buffer, so a
command producing gigabytes of output cannot spike memory or fill the disk:
we keep only the last MAX_OUTPUT_CHARS bytes (the tail, where a command's
conclusion / final error usually is) and count what was dropped. Draining the
full stream (rather than stopping early) keeps the child from blocking on a
full pipe; the overall timeout bounds wall-clock time.
"""

import os
import signal
import subprocess
import threading
from dataclasses import dataclass

READ_CHUNK = 65536

# On POSIX, start each command in its own session/process-group so we can kill
# the whole tree (shell + pipeline children), not just /bin/sh.
_NEW_SESSION = os.name == "posix"


# Secrets that must never leak into an executed command's environment.
# (API_KEY is the whole auth boundary; a read-only `env`/`cat /proc/self/environ`
# must not reveal it.) GITHUB_TOKEN is kept - `gh`/`git` children need it.
_STRIPPED_ENV_KEYS = frozenset({"API_KEY"})


def child_env() -> dict:
    """os.environ for child commands, with secret keys removed."""
    return {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV_KEYS}


def kill_process_tree(proc: "subprocess.Popen") -> None:
    """Kill a command's whole process group (POSIX) or the process (Windows)."""
    try:
        if _NEW_SESSION:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


@dataclass
class CommandResult:
    """Result of a command execution."""

    stdout: str
    stderr: str
    exit_code: int


def _output_limit() -> int:
    return int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))


class _CappedReader(threading.Thread):
    """Drain a binary stream fully while retaining only the last `cap` bytes."""

    def __init__(self, stream, cap: int):
        super().__init__(daemon=True)
        self._stream = stream
        self._cap = cap
        self._buf = bytearray()
        self._discarded = 0

    def run(self) -> None:
        try:
            while True:
                data = self._stream.read(READ_CHUNK)
                if not data:
                    break
                self._buf += data
                if self._cap and len(self._buf) > self._cap:
                    overflow = len(self._buf) - self._cap
                    del self._buf[:overflow]
                    self._discarded += overflow
        except (ValueError, OSError):
            pass  # stream closed under us (e.g. after the process was killed)

    def text(self) -> str:
        body = self._buf.decode("utf-8", errors="replace")
        if self._discarded:
            return f"[... {self._discarded} earlier bytes truncated ...]\n" + body
        return body


def execute(command: str, timeout: int | None = None) -> CommandResult:
    """
    Execute a shell command and capture output (bounded, streamed).

    Args:
        command: The shell command to execute.
        timeout: Optional timeout in seconds. None means no timeout.

    Returns:
        CommandResult with stdout, stderr, and exit_code (-1 on timeout).
    """
    cap = _output_limit()
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=_NEW_SESSION,
        env=child_env(),
    )
    out_reader = _CappedReader(proc.stdout, cap)
    err_reader = _CappedReader(proc.stderr, cap)
    out_reader.start()
    err_reader.start()

    try:
        proc.wait(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        proc.wait()
        exit_code = -1

    # Readers finish once the pipes reach EOF (which the exit/kill guarantees).
    out_reader.join()
    err_reader.join()

    return CommandResult(
        stdout=out_reader.text(),
        stderr=err_reader.text(),
        exit_code=exit_code,
    )
