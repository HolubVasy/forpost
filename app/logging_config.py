"""Logging configuration for ForPost.

Two separate log streams:
- commands.jsonl: audit trail of every executed command / file op
- app.log: ForPost's own operational/runtime logs

Both are size-bounded (MAX_LOG_BYTES per file):
- commands.jsonl self-trims - when it exceeds the budget the oldest
  LOG_TRIM_FRACTION of lines are dropped (a single, recent-heavy file, no
  extra generations).
- app.log rotates via a RotatingFileHandler (keeps one .1 backup).
Secrets in the audit are redacted (token prefixes masked).
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _get_logs_dir() -> Path:
    """Return the logs directory, creating it if needed."""
    logs_dir = Path(os.environ.get("LOGS_DIR", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def _max_log_bytes() -> int:
    return int(os.environ.get("MAX_LOG_BYTES", str(10 * 1024 * 1024)))


def _trim_fraction() -> float:
    return float(os.environ.get("LOG_TRIM_FRACTION", "0.2"))


# --- secret redaction -------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"gho_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(x-api-key:\s*)\S+"),
]


def redact(text: str) -> str:
    """Mask well-known secret token shapes before they hit the audit log."""
    if not text:
        return text

    def _sub(m: "re.Match") -> str:
        return (m.group(1) + "***REDACTED***") if m.groups() else "***REDACTED***"

    for pat in _SECRET_PATTERNS:
        text = pat.sub(_sub, text)
    return text


# --- app.log (operational) --------------------------------------------------

def get_app_logger() -> logging.Logger:
    """Get the application logger (writes to logs/app.log, size-rotated)."""
    logger = logging.getLogger("forpost.app")
    logs_dir = _get_logs_dir()
    target_path = str(logs_dir / "app.log")
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == target_path:
            return logger
    logger.handlers.clear()
    handler = RotatingFileHandler(
        target_path, maxBytes=_max_log_bytes(), backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


# --- commands.jsonl (audit) -------------------------------------------------

def _trim_commands(path: Path) -> None:
    """When the audit log exceeds the budget, drop the oldest fraction of lines
    (keeps a single, recent-heavy file). Runs only on overflow."""
    try:
        if not path.exists() or path.stat().st_size <= _max_log_bytes():
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        drop = int(len(lines) * _trim_fraction())
        path.write_text("".join(lines[drop:]), encoding="utf-8")
    except OSError:
        pass


def _append_command(entry: dict) -> None:
    logs_dir = _get_logs_dir()
    path = logs_dir / "commands.jsonl"
    _trim_commands(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def log_command(
    command: str,
    mode: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> None:
    """Append a command execution record to commands.jsonl (secrets redacted)."""
    _append_command({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": "execute_command",
        "mode": mode,
        "command": redact(command),
        "exit_code": exit_code,
        "stdout": redact(stdout),
        "stderr": redact(stderr),
    })


def log_file_operation(
    operation: str,
    path: str,
    mode: str,
    size: int | None = None,
) -> None:
    """Append a file operation record to commands.jsonl."""
    _append_command({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "mode": mode,
        "path": path,
        "size": size,
    })
