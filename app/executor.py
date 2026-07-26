"""Command execution via subprocess, with bounded output and timeout.

Output is drained on background threads into a fixed-size ring buffer, so a
command producing gigabytes of output cannot spike memory or fill the disk:
we keep only the last MAX_OUTPUT_CHARS bytes (the tail, where a command's
conclusion / final error usually is) and count what was dropped. Draining the
full stream (rather than stopping early) keeps the child from blocking on a
full pipe; the overall timeout bounds wall-clock time.
"""

import os
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass

READ_CHUNK = 65536

# On POSIX, start each command in its own session/process-group so we can kill
# the whole tree (shell + pipeline children), not just /bin/sh.
_NEW_SESSION = os.name == "posix"


# Secrets that must never sit in a command's environment. API_KEY is the whole
# auth boundary and no child ever needs it. Both git tokens are stripped from the
# base env; the one matching the requested scope is re-exposed (see child_env).
_STRIPPED_ENV_KEYS = frozenset({"API_KEY"})
_GIT_TOKEN_KEYS = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_WORK_TOKEN")

# Which env var supplies the token for each scope. "work" stays read-only in
# practice: it is only ever injected in whitelist mode, which allows read
# commands only - so ForPost can never *write* to the work repo.
_TOKEN_SOURCE = {"personal": "GITHUB_TOKEN", "work": "GITHUB_WORK_TOKEN"}


def child_env(git_token: str | None = None) -> dict:
    """os.environ for child commands, with secrets scoped per mode.

    API_KEY and every git token are stripped from the base env; then, for the
    requested scope, the matching token is re-exposed as both GITHUB_TOKEN and
    GH_TOKEN so git/gh pick it up.

    git_token: None (read-only - no token at all), "personal" (full-access - the
    personal token, full rights on your own repos), or "work" (whitelist - the
    work token, read-only in practice).
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _STRIPPED_ENV_KEYS and k not in _GIT_TOKEN_KEYS
    }
    source = _TOKEN_SOURCE.get(git_token or "")
    if source:
        value = os.environ.get(source)
        if value:
            env["GITHUB_TOKEN"] = value
            env["GH_TOKEN"] = value
    return env


def _restricted_user() -> str:
    """The unprivileged user restricted-mode (read-only/whitelist) commands run
    as, so they cannot read the app's env (/proc/1/environ) or docker socket.
    Empty (local dev / Windows) means "run in-process, no privilege drop"."""
    return os.environ.get("RESTRICTED_COMMAND_USER", "").strip()


def _can_drop_privileges() -> bool:
    return bool(_restricted_user()) and _NEW_SESSION and shutil.which("sudo") is not None


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


def build_popen_args(command: str, restricted: bool, git_token: str | None) -> dict:
    """Popen kwargs for a command. In restricted mode (read-only/whitelist) the
    command is run as the unprivileged RESTRICTED_COMMAND_USER via `sudo -u`, so
    it cannot read the app's environment (/proc/1/environ) or the docker socket.
    Without a configured user / sudo (local dev, Windows) it runs in-process."""
    env = child_env(git_token)
    if restricted and _can_drop_privileges():
        # -n: never prompt (NOPASSWD sudoers rule); -H: set HOME so gh/git have
        # a writable config dir.
        argv = ["sudo", "-n", "-H", "-u", _restricted_user(), "bash", "-c", command]
        return {"args": argv, "shell": False, "env": env}
    return {"args": command, "shell": True, "env": env}


def execute(
    command: str,
    timeout: int | None = None,
    restricted: bool = False,
    git_token: str | None = None,
) -> CommandResult:
    """
    Execute a shell command and capture output (bounded, streamed).

    Args:
        command: The shell command to execute.
        timeout: Optional timeout in seconds. None means no timeout.
        restricted: Run as the unprivileged restricted user (read-only/whitelist).
        git_token: Token scope to expose - None / "personal" / "work".

    Returns:
        CommandResult with stdout, stderr, and exit_code (-1 on timeout).
    """
    cap = _output_limit()
    popen = build_popen_args(command, restricted, git_token)
    proc = subprocess.Popen(
        popen["args"],
        shell=popen["shell"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=_NEW_SESSION,
        env=popen["env"],
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
