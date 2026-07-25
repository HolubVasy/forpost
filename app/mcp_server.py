"""MCP wrapper for ForPost.

Exposes the same functionality as the HTTP endpoints (main.py) as MCP tools
Claude can call: execute_command plus the three file operations. This layer
adds no logic of its own - it orchestrates the exact same Phase-1 building
blocks (validate_command, execute, file_ops, logging, rate_limiter) that the
HTTP endpoints use, so both surfaces behave identically.

Auth note: a single static API key is the auth model (per SPEC). For the MCP
surface the key is enforced at the HTTP transport layer (reverse proxy /
connector settings send it as a header once), not as a per-tool argument -
keeping the tool schema clean and matching how MCP connectors are configured.
"""

from dotenv import load_dotenv

load_dotenv()

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from app.executor import execute
from app.file_ops import FileEncoding, delete_file, read_file, write_file
from app.logging_config import log_command, log_file_operation
from app.modes import Mode, validate_command
from app.rate_limit import rate_limiter

mcp = FastMCP("ForPost")

# Hard timeout for every command, per SPEC (5 minutes, not configurable).
COMMAND_TIMEOUT = 300


def _guard() -> None:
    """Shared pre-flight: rate limit. Converts HTTP errors to MCP errors."""
    try:
        rate_limiter.check()
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc


@mcp.tool()
def execute_command(command: str, mode: Mode) -> dict:
    """Run a shell command on the VPS and return its output.

    Args:
        command: The shell command to run.
        mode: One of "read-only", "whitelist", or "full-access".

    Returns stdout, stderr, exit_code, and timed_out.
    """
    _guard()

    allowed, reason = validate_command(command, mode)
    if not allowed:
        raise ValueError(reason)

    result = execute(command, timeout=COMMAND_TIMEOUT)
    log_command(
        command=command,
        mode=mode.value,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.exit_code == -1,
    }


@mcp.tool()
def write_file_tool(
    path: str,
    content: str,
    mode: Mode,
    encoding: FileEncoding = FileEncoding.TEXT,
) -> dict:
    """Write content to a file within the workspace.

    Args:
        path: Destination path (relative to the workspace root).
        content: File content. Plain text, or base64 when encoding="base64".
        mode: Request mode. Rejected in "read-only".
        encoding: "text" (default) or "base64" for binary files.

    Returns the resolved path and byte size written.
    """
    _guard()

    if mode == Mode.READ_ONLY:
        raise ValueError("write_file not allowed in read-only mode")

    try:
        result = write_file(path, content, encoding)
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc

    log_file_operation("write_file", result["path"], mode.value, result["size"])
    return result


@mcp.tool()
def read_file_tool(
    path: str,
    mode: Mode,
    encoding: FileEncoding = FileEncoding.TEXT,
) -> dict:
    """Read a file within the workspace. Allowed in all modes.

    Args:
        path: Path to read (relative to the workspace root).
        mode: Request mode (read_file is permitted in every mode).
        encoding: "text" (default) or "base64" for binary files.

    Returns the resolved path, content, and byte size.
    """
    _guard()

    try:
        result = read_file(path, encoding)
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc

    log_file_operation("read_file", result["path"], mode.value, result["size"])
    return result


@mcp.tool()
def delete_file_tool(path: str, mode: Mode) -> dict:
    """Delete a file within the workspace.

    Args:
        path: Path to delete (relative to the workspace root).
        mode: Request mode. Rejected in "read-only".

    Returns the resolved path deleted.
    """
    _guard()

    if mode == Mode.READ_ONLY:
        raise ValueError("delete_file not allowed in read-only mode")

    try:
        result = delete_file(path)
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc

    log_file_operation("delete_file", result["path"], mode.value)
    return result


if __name__ == "__main__":
    import os

    # stdio for local/desktop connectors; streamable-http for remote VPS.
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
