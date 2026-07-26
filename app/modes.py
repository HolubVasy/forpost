"""Mode validation logic for execute_command."""

import os
from enum import Enum
from pathlib import Path


class Mode(str, Enum):
    READ_ONLY = "read-only"
    WHITELIST = "whitelist"
    FULL_ACCESS = "full-access"


# Explicit set of allowed read command prefixes for read-only mode.
# Based on an allowlist approach (not a denylist of write commands).
# read-only runs as the unprivileged RESTRICTED_COMMAND_USER (no docker group),
# so docker commands are intentionally absent - docker inspection is full-access.
ALLOWED_READ_PREFIXES = (
    "cat ",
    "df ",
    "df",
    "echo ",
    # NOTE: `docker *`, `docker inspect` and `env` are intentionally NOT here -
    # docker needs the socket (root-equivalent) and `env`/`docker inspect` leak
    # secrets. `find` is excluded too (`find -delete`/`-exec` would make
    # read-only destructive). Use full-access for any of these.
    "git branch",
    "git diff",
    "git log",
    "git show",
    "git status",
    "head ",
    "hostname",
    "id",
    "ls",
    "ps ",
    "ps",
    "pwd",
    "tail ",
    "uname",
    "uptime",
    "wc ",
    "which ",
    "who",
    "whoami",
)


# Shell metacharacters that enable command chaining, substitution, or
# redirection. Rejected in read-only/whitelist so an allowed prefix can't be
# used to smuggle a second command (e.g. "ls; rm -rf" or "git && curl ...").
# This is "protection from self", not a sandbox: it does NOT inspect an allowed
# command's own flags. full-access is the deliberate escape hatch for
# chaining/pipes.
_SHELL_METACHARS = frozenset(";|&`$()<>\n\r")


def _has_shell_metachars(command: str) -> bool:
    return any(ch in _SHELL_METACHARS for ch in command)


def _prefix_allowed(command: str, prefixes) -> bool:
    """Match on a TOKEN boundary, not a bare string prefix, so `ps` does not
    allow `psql` and `ls` does not allow `lsof`."""
    for prefix in prefixes:
        p = prefix.rstrip()
        if command == p or command.startswith(p + " "):
            return True
    return False


def _load_whitelist() -> list[str]:
    """Load allowed command prefixes from whitelist.config."""
    config_path = Path(os.environ.get("WHITELIST_PATH", "whitelist.config"))
    if not config_path.exists():
        return []
    prefixes = []
    for line in config_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            prefixes.append(line)
    return prefixes


def validate_command(command: str, mode: Mode) -> tuple[bool, str]:
    """
    Validate whether a command is allowed under the given mode.

    Returns (allowed: bool, reason: str).
    If allowed, reason is empty. If rejected, reason explains why.
    """
    command = command.strip()
    if not command:
        return False, "Empty command"

    if mode == Mode.FULL_ACCESS:
        return True, ""

    # read-only and whitelist must not allow shell chaining/substitution.
    if _has_shell_metachars(command):
        return False, (
            "Shell metacharacters (; | & ` $ ( ) < >) are not allowed in "
            "read-only/whitelist mode - use full-access for chaining/pipes"
        )

    if mode == Mode.READ_ONLY:
        if _prefix_allowed(command, ALLOWED_READ_PREFIXES):
            return True, ""
        return False, f"Command not allowed in read-only mode: {command.split()[0]}"

    if mode == Mode.WHITELIST:
        whitelist = _load_whitelist()
        if not whitelist:
            return False, "Whitelist is empty or missing - fail closed"
        if _prefix_allowed(command, whitelist):
            return True, ""
        return False, f"Command not in whitelist: {command.split()[0]}"

    return False, f"Unknown mode: {mode}"
