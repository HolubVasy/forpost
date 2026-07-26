"""Application service layer - the single place a request is orchestrated.

Both the REST endpoints (main.py) and the MCP tools (mcp_server.py) are thin
adapters over these functions: validation, execution, jobs, file ops, timeout
clamping, and logging live here once, so the two surfaces cannot drift.

Cross-cutting transport concerns (auth, rate limiting) stay in the transports;
domain failures are raised as ForPostError and translated there.
"""

import os

from app.errors import ForPostError
from app.executor import execute
from app.file_ops import FileEncoding, delete_file, read_file, write_file
from app.jobs import job_manager
from app.logging_config import log_command, log_file_operation
from app.modes import Mode, validate_command

DEFAULT_TIMEOUT = 300
DEFAULT_JOB_TIMEOUT = 1800


def clamp_timeout(requested: int) -> int:
    """Clamp a requested timeout to [1, MAX_TIMEOUT_SECONDS] (default 1800)."""
    hard_max = int(os.environ.get("MAX_TIMEOUT_SECONDS", "1800"))
    return max(1, min(int(requested), hard_max))


def run_command(command: str, mode: Mode, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Validate, run synchronously, log, and return the result."""
    allowed, reason = validate_command(command, mode)
    if not allowed:
        raise ForPostError(403, reason)
    result = execute(command, timeout=clamp_timeout(timeout))
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


def start_job(command: str, mode: Mode, timeout: int = DEFAULT_JOB_TIMEOUT) -> dict:
    """Validate and start a background job; returns its id."""
    allowed, reason = validate_command(command, mode)
    if not allowed:
        raise ForPostError(403, reason)
    job_id = job_manager.start(command, clamp_timeout(timeout))
    log_command(
        command=command,
        mode=mode.value,
        exit_code=0,
        stdout=f"[async job started: {job_id}]",
        stderr="",
    )
    return {"job_id": job_id, "status": "running"}


def get_job_status(job_id: str, offset: int = 0) -> dict:
    try:
        return job_manager.status(job_id, offset)
    except KeyError:
        raise ForPostError(404, f"No such job: {job_id}")


def kill_job(job_id: str) -> dict:
    try:
        return job_manager.kill(job_id)
    except KeyError:
        raise ForPostError(404, f"No such job: {job_id}")


def write(path: str, content: str, encoding: FileEncoding, mode: Mode) -> dict:
    if mode == Mode.READ_ONLY:
        raise ForPostError(403, "write_file not allowed in read-only mode")
    result = write_file(path, content, encoding)
    log_file_operation("write_file", result["path"], mode.value, result["size"])
    return result


def read(path: str, encoding: FileEncoding, mode: Mode) -> dict:
    result = read_file(path, encoding)
    log_file_operation("read_file", result["path"], mode.value, result["size"])
    return result


def delete(path: str, mode: Mode) -> dict:
    if mode == Mode.READ_ONLY:
        raise ForPostError(403, "delete_file not allowed in read-only mode")
    result = delete_file(path)
    log_file_operation("delete_file", result["path"], mode.value)
    return result
