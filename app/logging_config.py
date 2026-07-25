"""Logging configuration for ForPost.

Two separate log streams:
- commands.jsonl: audit trail of every executed command
- app.log: ForPost's own operational/runtime logs
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


def _get_logs_dir() -> Path:
    """Return the logs directory, creating it if needed."""
    logs_dir = Path(os.environ.get("LOGS_DIR", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_app_logger() -> logging.Logger:
    """Get the application logger (writes to logs/app.log)."""
    logger = logging.getLogger("forpost.app")
    # Always reconfigure to pick up current LOGS_DIR
    logs_dir = _get_logs_dir()
    target_path = str(logs_dir / "app.log")
    # Only add handler if not already pointing to the correct file
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == target_path:
            return logger
    # Remove stale handlers
    logger.handlers.clear()
    handler = logging.FileHandler(target_path)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def log_command(
    command: str,
    mode: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> None:
    """Append a command execution record to commands.jsonl."""
    logs_dir = _get_logs_dir()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": "execute_command",
        "mode": mode,
        "command": command,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }
    with open(logs_dir / "commands.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def log_file_operation(
    operation: str,
    path: str,
    mode: str,
    size: int | None = None,
) -> None:
    """Append a file operation record to commands.jsonl."""
    logs_dir = _get_logs_dir()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "mode": mode,
        "path": path,
        "size": size,
    }
    with open(logs_dir / "commands.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
