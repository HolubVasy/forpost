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
    """Return the workspace root directory (matches the Dockerfile default)."""
    return Path(os.environ.get("WORKSPACE_ROOT", "/home/forpost/workspace"))


def _resolve_path(path: str) -> Path:
    """Resolve a path and confine it to the workspace.

    Uses is_relative_to (not a string prefix): a plain startswith would let a
    sibling like `<workspace>-evil` slip through, and it follows symlinks via
    resolve() so a symlink escaping the workspace is rejected too.
    """
    workspace = _get_workspace_root().resolve()
    resolved = (workspace / path).resolve()
    if not resolved.is_relative_to(workspace):
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


def read_file(
    path: str,
    encoding: FileEncoding,
    offset: int = 0,
    length: int | None = None,
) -> dict:
    """Read (a bounded slice of) a file.

    Reads at most `length` bytes from `offset` (default: MAX_OUTPUT_CHARS,
    clamped to it) so a huge file can't blow up memory or the response/context.
    `size` is the total file size; `next_offset` + `truncated` let the caller
    paginate, mirroring job_status.
    """
    resolved = _resolve_path(path)

    if not resolved.exists():
        raise ForPostError(404, f"File not found: {path}")
    if resolved.is_dir():
        raise ForPostError(400, "Cannot read a directory")

    cap = int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))
    n = cap if length is None else max(0, min(int(length), cap))
    offset = max(0, int(offset))
    total = resolved.stat().st_size

    with open(resolved, "rb") as f:
        f.seek(offset)
        data = f.read(n)

    if encoding == FileEncoding.BASE64:
        content = base64.b64encode(data).decode("ascii")
    else:
        content = data.decode("utf-8", errors="replace")

    next_offset = offset + len(data)
    return {
        "path": str(resolved),
        "content": content,
        "size": total,
        "offset": offset,
        "next_offset": next_offset,
        "truncated": next_offset < total,
    }


def delete_file(path: str) -> dict:
    """Delete a file. Returns the path deleted."""
    resolved = _resolve_path(path)

    if not resolved.exists():
        raise ForPostError(404, f"File not found: {path}")
    if resolved.is_dir():
        raise ForPostError(400, "Cannot delete a directory")

    resolved.unlink()
    return {"path": str(resolved)}
