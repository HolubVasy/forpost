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

import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from app.executor import execute
from app.file_ops import FileEncoding, delete_file, read_file, write_file
from app.jobs import job_manager
from app.logging_config import log_command, log_file_operation
from app.modes import Mode, validate_command

# stateless_http: each request is independent (no server-side session state) -
#   the right fit for a remote connector calling a stateless command tunnel.
# streamable_http_path "/": the ASGI sub-app serves at its own root, so mounting
#   it under "/mcp" in main.py yields the public endpoint /mcp (not /mcp/mcp).
# transport_security: disable DNS-rebinding / Host-header checks. ForPost runs
#   behind the nginx reverse proxy (Host = forpost.poly-mind.net) and is guarded
#   by X-API-Key; the default localhost-only Host check would 421 real traffic.
# Server-level manual, returned in the MCP `initialize` result as `instructions`
# so any client (and the model) learns how ForPost works without out-of-band docs.
INSTRUCTIONS = """\
ForPost is a command-line tunnel into a VPS: call a tool, a shell command runs
on the server, you get stdout/stderr/exit_code back. It executes and returns -
no retries, no rollback; all reasoning is on the caller's side.

MODES (per call, required):
- read-only   : inspection only (ls, cat, docker ps/logs, git status...).
                Write commands and shell metacharacters (; | & ` $ () <>) are
                rejected. A guardrail.
- whitelist   : only commands matching the server's whitelist.config; shell
                metacharacters likewise rejected. A guardrail.
- full-access : anything, executed immediately. No guardrail - use consciously.

SYNC vs ASYNC:
- execute_command: run and wait for the result. Per-call `timeout` (default
  300s, max 1800s); on timeout the process is killed and partial output returns
  with timed_out=true.
- run_async / job_status / job_kill: for long tasks (docker build, deploys)
  that outlive one request. run_async returns a job_id immediately; poll
  job_status(job_id, offset) to follow output as it grows (offset also
  paginates); job_kill stops it.

FILES: write_file / read_file / delete_file move bytes within the workspace
(text or base64), subject to the same mode rules (read-only can only read).

LIMITS: output is capped (tail kept) to bound memory and context; every call is
logged. Auth is a static X-API-Key header, configured once in the connector.

See the forpost://manual resource for a fuller reference.
"""

mcp = FastMCP(
    "ForPost",
    instructions=INSTRUCTIONS,
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# Default per-call timeout (seconds); callers may override, clamped to the max.
DEFAULT_TIMEOUT = 300


def _clamp_timeout(requested: int) -> int:
    hard_max = int(os.environ.get("MAX_TIMEOUT_SECONDS", "1800"))
    return max(1, min(int(requested), hard_max))

# Note: auth + rate limiting for the MCP surface are applied once at the ASGI
# transport guard (main.py MCPApiKeyGuard), not per tool - so tools don't
# re-check (which would double-count against the shared rate limiter).


@mcp.tool(
    title="Execute shell command (sync)",
    annotations=ToolAnnotations(
        title="Execute shell command (sync)",
        readOnlyHint=False,  # can write/delete in whitelist/full-access
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def execute_command(command: str, mode: Mode, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run a shell command on the VPS and return its output.

    Args:
        command: The shell command to run.
        mode: One of "read-only", "whitelist", or "full-access".
        timeout: Per-call timeout in seconds (default 300, clamped to the
            server max). On timeout the process is killed and partial output is
            returned with timed_out=true.

    Returns stdout, stderr, exit_code, and timed_out.
    """
    allowed, reason = validate_command(command, mode)
    if not allowed:
        raise ValueError(reason)

    result = execute(command, timeout=_clamp_timeout(timeout))
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


@mcp.tool(
    title="Write file",
    annotations=ToolAnnotations(
        title="Write file",
        readOnlyHint=False,
        destructiveHint=True,  # overwrites an existing file at the path
        idempotentHint=True,
        openWorldHint=False,
    ),
)
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
    if mode == Mode.READ_ONLY:
        raise ValueError("write_file not allowed in read-only mode")

    try:
        result = write_file(path, content, encoding)
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc

    log_file_operation("write_file", result["path"], mode.value, result["size"])
    return result


@mcp.tool(
    title="Read file",
    annotations=ToolAnnotations(
        title="Read file",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
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
    try:
        result = read_file(path, encoding)
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc

    log_file_operation("read_file", result["path"], mode.value, result["size"])
    return result


@mcp.tool(
    title="Delete file",
    annotations=ToolAnnotations(
        title="Delete file",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def delete_file_tool(path: str, mode: Mode) -> dict:
    """Delete a file within the workspace.

    Args:
        path: Path to delete (relative to the workspace root).
        mode: Request mode. Rejected in "read-only".

    Returns the resolved path deleted.
    """
    if mode == Mode.READ_ONLY:
        raise ValueError("delete_file not allowed in read-only mode")

    try:
        result = delete_file(path)
    except HTTPException as exc:
        raise ValueError(exc.detail) from exc

    log_file_operation("delete_file", result["path"], mode.value)
    return result


# --- Background jobs (async model for long-running commands) ---


@mcp.tool(
    title="Run command in background (async)",
    annotations=ToolAnnotations(
        title="Run command in background (async)",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def run_async(command: str, mode: Mode, timeout: int = 1800) -> dict:
    """Start a long-running command in the background and return a job_id at once.

    Use this instead of execute_command for tasks that outlive a single request
    (e.g. `docker build`). Follow progress with job_status(job_id, offset); stop
    it with job_kill(job_id).

    Args:
        command: The shell command to run.
        mode: One of "read-only", "whitelist", or "full-access".
        timeout: Max seconds before the job is auto-killed (default 1800).

    Returns job_id and status.
    """
    allowed, reason = validate_command(command, mode)
    if not allowed:
        raise ValueError(reason)
    job_id = job_manager.start(command, _clamp_timeout(timeout))
    log_command(
        command=command,
        mode=mode.value,
        exit_code=0,
        stdout=f"[async job started: {job_id}]",
        stderr="",
    )
    return {"job_id": job_id, "status": "running"}


@mcp.tool(
    title="Get background job status/output",
    annotations=ToolAnnotations(
        title="Get background job status/output",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def job_status(job_id: str, offset: int = 0) -> dict:
    """Get a background job's status and output produced since `offset`.

    Poll this to follow a running job's output; `next_offset` is where to
    resume next time (which also paginates a large finished output).

    Args:
        job_id: The id returned by run_async.
        offset: Byte offset to read the output log from (default 0).

    Returns status, running, exit_code, output, offset, next_offset,
    total_bytes, truncated.
    """
    try:
        return job_manager.status(job_id, offset)
    except KeyError:
        raise ValueError(f"No such job: {job_id}")


@mcp.tool(
    title="Kill background job",
    annotations=ToolAnnotations(
        title="Kill background job",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def job_kill(job_id: str) -> dict:
    """Terminate a running background job. Returns job_id and status."""
    try:
        return job_manager.kill(job_id)
    except KeyError:
        raise ValueError(f"No such job: {job_id}")


@mcp.resource(
    "forpost://manual",
    name="ForPost manual",
    title="ForPost manual",
    description="Human-readable reference: modes, sync vs async jobs, files, limits.",
    mime_type="text/markdown",
)
def manual() -> str:
    """Full ForPost reference, fetchable by clients as an MCP resource."""
    return """\
# ForPost - MCP tool reference

A command-line tunnel into a VPS. Tools execute and return; no retries/rollback.

## Modes (per call, required)
| mode | allows | guardrail |
|------|--------|-----------|
| read-only | inspection only (ls, cat, docker ps/logs, git status, df, ps...) | writes + shell metacharacters `; | & \\` $ () <>` rejected |
| whitelist | commands matching the server's whitelist.config | metacharacters likewise rejected |
| full-access | anything, immediately | none - use consciously |

Guardrails are "protection from self", not a sandbox: an allowed command's own
dangerous flags (e.g. `find -delete`) are not neutered.

## Sync execution - execute_command(command, mode, timeout=300)
Runs and waits. Returns `{stdout, stderr, exit_code, timed_out}`. Timeout in
seconds (default 300, max 1800); on timeout the process is killed and partial
output returns with `timed_out=true`. Output is capped (tail kept).

## Async jobs - for long tasks (docker build, deploys)
- `run_async(command, mode, timeout=1800)` -> `{job_id, status:"running"}` immediately.
- `job_status(job_id, offset=0)` -> `{status, running, exit_code, output,
  offset, next_offset, total_bytes, truncated}`. Poll it; pass the previous
  `next_offset` to follow new output (and to paginate).
- `job_kill(job_id)` -> stops it.
Job state is in-memory (lost on server restart); output logs persist on disk.

## Files - within the workspace, subject to mode rules
- `write_file_tool(path, content, mode, encoding="text"|"base64")`
- `read_file_tool(path, mode, encoding)` (allowed in all modes)
- `delete_file_tool(path, mode)`
Paths are confined to the workspace; traversal/symlink escape is rejected.

## Auth & limits
Static `X-API-Key` header (set once in the connector). Rate limited; every call
is logged. Prefer read-only/whitelist when you want a brake.
"""


if __name__ == "__main__":
    # stdio for local/desktop connectors; streamable-http for remote VPS.
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
