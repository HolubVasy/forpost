"""File operations for ForPost (write_file, read_file, delete_file)."""

import base64
import os
from enum import Enum
from pathlib import Path

from app.errors import ForPostError


class FileEncoding(str, Enum):
    TEXT = "text"
    BASE64 = "base64"


def _get_workspace_root() -> Path:
    """Return the workspace root directory."""
    return Path(os.environ.get("WORKSPACE_ROOT", "/home/forpost"))


def _resolve_path(path: str) -> Path:
    """Resolve and validate a file path within the workspace."""
    workspace = _get_workspace_root()
    resolved = (workspace / path).resolve()
    # Ensure path doesn't escape the workspace
    if not str(resolved).startswith(str(workspace.resolve())):
        raise ForPostError(403, "Path escapes workspace boundary")
    return resolved


def write_file(path: str, content: str, encoding: FileEncoding) -> dict:
    """Write content to a file. Returns path and size."""
    resolved = _resolve_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    if encoding == FileEncoding.BASE64:
        data = base64.b64decode(content)
        resolved.write_bytes(data)
        size = len(data)
    else:
        resolved.write_text(content, encoding="utf-8")
        size = len(content.encode("utf-8"))

    return {"path": str(resolved), "size": size}


def read_file(path: str, encoding: FileEncoding) -> dict:
    """Read a file and return its content."""
    resolved = _resolve_path(path)

    if not resolved.exists():
        raise ForPostError(404, f"File not found: {path}")

    if encoding == FileEncoding.BASE64:
        data = resolved.read_bytes()
        content = base64.b64encode(data).decode("ascii")
        size = len(data)
    else:
        content = resolved.read_text(encoding="utf-8")
        size = len(content.encode("utf-8"))

    return {"path": str(resolved), "content": content, "size": size}


def delete_file(path: str) -> dict:
    """Delete a file. Returns the path deleted."""
    resolved = _resolve_path(path)

    if not resolved.exists():
        raise ForPostError(404, f"File not found: {path}")
    if resolved.is_dir():
        raise ForPostError(400, "Cannot delete a directory")

    resolved.unlink()
    return {"path": str(resolved)}
