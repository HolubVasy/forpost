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
ALLOWED_READ_PREFIXES = (
    "cat ",
    "df ",
    "df",
    "docker inspect",
    "docker logs",
    "docker ps",
    "echo ",
    "env",
    "find ",
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
# This is "protection from self", not a sandbox: it does NOT stop dangerous
# flags of an otherwise-allowed command (e.g. `find -delete`). full-access is
# the deliberate escape hatch when you really need chaining/pipes.
_SHELL_METACHARS = frozenset(";|&`$()<>\n\r")


def _has_shell_metachars(command: str) -> bool:
    return any(ch in _SHELL_METACHARS for ch in command)


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
        for prefix in ALLOWED_READ_PREFIXES:
            if command == prefix or command.startswith(prefix):
                return True, ""
        return False, f"Command not allowed in read-only mode: {command.split()[0]}"

    if mode == Mode.WHITELIST:
        whitelist = _load_whitelist()
        if not whitelist:
            return False, "Whitelist is empty or missing - fail closed"
        for prefix in whitelist:
            if command == prefix or command.startswith(prefix):
                return True, ""
        return False, f"Command not in whitelist: {command.split()[0]}"

    return False, f"Unknown mode: {mode}"
