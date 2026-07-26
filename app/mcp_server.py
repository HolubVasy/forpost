"""MCP wrapper for ForPost.

Exposes ForPost's functionality as MCP tools Claude can call. Each tool is a
thin adapter over the shared service layer (app/service.py) - the same layer the
REST endpoints (main.py) use - so both surfaces behave identically. This module
owns only the MCP-specific concerns: tool schemas, titles, annotations, the
server manual, and translating domain errors into ToolErrors.

Auth note: a single static API key is the auth model (per SPEC). For the MCP
surface the key is enforced at the HTTP transport layer (reverse proxy /
connector settings send it as a header once), not as a per-tool argument -
keeping the tool schema clean and matching how MCP connectors are configured.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from app import service
from app.errors import ForPostError
from app.file_ops import FileEncoding
from app.modes import Mode
from app.service import DEFAULT_JOB_TIMEOUT, DEFAULT_TIMEOUT

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
(text or base64). read is allowed in any mode; write and delete require
full-access (whitelist must not be able to plant files).

HEALTH: diagnostics() returns a one-call snapshot (alive?, memory, jobs
running/stuck, versions, recent failures) - read it instead of digging through
logs. reset(scope="jobs"|"logs"|"all") clears own state: stuck jobs, logs, or all.

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

# Auth + rate limiting for the MCP surface are applied once at the ASGI transport
# guard (main.py MCPApiKeyGuard), not per tool. Each tool below is a thin adapter
# over the shared service layer; domain errors become ToolErrors via _call.


def _call(fn, *args, **kwargs):
    """Run a service function, translating ForPostError -> ValueError (ToolError)."""
    try:
        return fn(*args, **kwargs)
    except ForPostError as exc:
        raise ValueError(exc.detail) from exc


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
    return _call(service.run_command, command, mode, timeout)


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
        mode: Must be "full-access" (writes are gated to avoid escalation).
        encoding: "text" (default) or "base64" for binary files.

    Returns the resolved path and byte size written.
    """
    return _call(service.write, path, content, encoding, mode)


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
    offset: int = 0,
    length: int | None = None,
) -> dict:
    """Read a bounded slice of a file within the workspace. Allowed in all modes.

    Reads at most `length` bytes (default: the server output cap) from `offset`,
    so a huge file can't blow up memory/context. Paginate a large file by
    passing the previous `next_offset`.

    Args:
        path: Path to read (relative to the workspace root).
        mode: Request mode (read_file is permitted in every mode).
        encoding: "text" (default) or "base64" for binary files.
        offset: Byte offset to start reading from (default 0).
        length: Max bytes to read (default/clamped to the server cap).

    Returns path, content, size (total), offset, next_offset, truncated.
    """
    return _call(service.read, path, encoding, mode, offset, length)


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
        mode: Must be "full-access" (deletes are gated).

    Returns the resolved path deleted.
    """
    return _call(service.delete, path, mode)


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
def run_async(command: str, mode: Mode, timeout: int = DEFAULT_JOB_TIMEOUT) -> dict:
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
    return _call(service.start_job, command, mode, timeout)


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
    return _call(service.get_job_status, job_id, offset)


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
    return _call(service.kill_job, job_id)


@mcp.tool(
    title="Diagnostics / health snapshot",
    annotations=ToolAnnotations(
        title="Diagnostics / health snapshot",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def diagnostics() -> dict:
    """One-call health image - read this instead of digging through logs.

    Returns: `ok` (alive & healthy), `uptime_seconds`, `checks`
    (docker / workspace / logs writable), `memory` (process RSS + system
    available/total), `jobs` (running / by-status / zombie count), `recent`
    (last commands: how many failed + the last failure, and the last app
    WARNING/ERROR), tool `versions`, and `commands_log_bytes`.
    """
    return service.diagnostics()


@mcp.tool(
    title="Reset ForPost state",
    annotations=ToolAnnotations(
        title="Reset ForPost state",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def reset(scope: str = "jobs") -> dict:
    """Reset ForPost's own state (never touches the host).

    Args:
        scope: "jobs" - kill all running jobs + wipe job state and job logs;
               "logs" - truncate command + app logs (and rotated backups);
               "workspace" - delete everything under the managed workspace;
               "all" - full cleanup (jobs + logs + workspace).

    Returns what was cleared.
    """
    return _call(service.reset, scope)


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

## Files - within the workspace
- `write_file_tool(path, content, mode, encoding="text"|"base64")` - full-access only.
- `read_file_tool(path, mode, encoding, offset=0, length=None)` - any mode.
  Reads are bounded (default = the output cap); paginate a big file by passing
  the previous `next_offset`.
- `delete_file_tool(path, mode)` - full-access only.
Paths are confined to the workspace (is_relative_to check); traversal and
symlink escape are rejected.

## Health
- `diagnostics()` - one-call snapshot: `ok`, uptime, checks (docker/workspace/
  logs), memory (process + system), jobs (running/stuck/zombies), recent
  failures, tool versions. Read this instead of the logs. Also `GET /diagnostics`.
- `reset(scope="jobs"|"logs"|"all")` - clear own state: jobs (kill+wipe),
  logs (truncate command+app logs), or all.

## Auth & limits
Static `X-API-Key` header (set once in the connector). Rate limited; every call
is logged. Prefer read-only/whitelist when you want a brake.
"""


if __name__ == "__main__":
    # stdio for local/desktop connectors; streamable-http for remote VPS.
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
