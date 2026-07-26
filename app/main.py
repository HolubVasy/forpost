"""ForPost - main FastAPI application."""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel


def _clamp_timeout(requested: int) -> int:
    """Clamp a per-request timeout to [1, MAX_TIMEOUT_SECONDS] (default 1800)."""
    hard_max = int(os.environ.get("MAX_TIMEOUT_SECONDS", "1800"))
    return max(1, min(int(requested), hard_max))

from app.auth import get_api_key, verify_api_key
from app.executor import execute
from app.file_ops import FileEncoding, delete_file, read_file, write_file
from app.logging_config import get_app_logger, log_command, log_file_operation
from app.mcp_server import mcp
from app.modes import Mode, validate_command
from app.rate_limit import rate_limiter

# Streamable-HTTP ASGI app exposing the MCP tools (execute_command + file ops).
# Mounted at /mcp below so Claude custom connectors can reach it at
# https://<host>/mcp, sharing the exact same logic as the REST endpoints.
mcp_app = mcp.streamable_http_app()


class MCPApiKeyGuard:
    """ASGI middleware requiring a valid X-API-Key on the mounted /mcp app.

    The FastAPI dependency (verify_api_key) only guards the REST routes; the
    mounted MCP sub-app bypasses it, so without this every MCP call would be
    unauthenticated. Same static-key model as the rest of ForPost.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"x-api-key", b"").decode()
            try:
                expected = get_api_key()
            except RuntimeError:
                expected = None
            if not expected or provided != expected:
                get_app_logger().warning("MCP request rejected: missing/invalid API key")
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"Missing or invalid API key"}',
                })
                return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(application: FastAPI):
    get_app_logger().info("ForPost started")
    # The MCP session manager must run for the streamable-HTTP endpoint to work.
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="ForPost", version="0.1.0", lifespan=lifespan)
app.mount("/mcp", MCPApiKeyGuard(mcp_app))

logger = get_app_logger()


@app.get("/health")
async def health():
    """Unauthenticated liveness check for container healthchecks / proxies."""
    return {"status": "ok"}


class ExecuteRequest(BaseModel):
    command: str
    mode: Mode
    # Per-call timeout in seconds (default 5 min, clamped to MAX_TIMEOUT_SECONDS).
    timeout: int = 300


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


@app.post("/execute", response_model=ExecuteResponse)
async def execute_command(
    request: ExecuteRequest,
    api_key: str = Depends(verify_api_key),
):
    # Rate limit check
    rate_limiter.check()

    # Validate command against mode
    allowed, reason = validate_command(request.command, request.mode)
    if not allowed:
        logger.warning(f"Command rejected: {reason}")
        raise HTTPException(status_code=403, detail=reason)

    # Execute in a worker thread so a long command doesn't block the event
    # loop (and /health). Timeout is per-call, clamped to the hard max.
    timeout = _clamp_timeout(request.timeout)
    result = await run_in_threadpool(execute, request.command, timeout)
    timed_out = result.exit_code == -1

    # Log the command execution
    log_command(
        command=request.command,
        mode=request.mode.value,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    return ExecuteResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        timed_out=timed_out,
    )


# --- File Operations ---


class WriteFileRequest(BaseModel):
    path: str
    content: str
    encoding: FileEncoding = FileEncoding.TEXT
    mode: Mode


class WriteFileResponse(BaseModel):
    path: str
    size: int


class ReadFileRequest(BaseModel):
    path: str
    encoding: FileEncoding = FileEncoding.TEXT
    mode: Mode


class ReadFileResponse(BaseModel):
    path: str
    content: str
    size: int


class DeleteFileRequest(BaseModel):
    path: str
    mode: Mode


class DeleteFileResponse(BaseModel):
    path: str


@app.post("/write_file", response_model=WriteFileResponse)
async def write_file_endpoint(
    request: WriteFileRequest,
    api_key: str = Depends(verify_api_key),
):
    rate_limiter.check()

    # read-only mode cannot write
    if request.mode == Mode.READ_ONLY:
        raise HTTPException(status_code=403, detail="write_file not allowed in read-only mode")

    result = write_file(request.path, request.content, request.encoding)
    log_file_operation("write_file", result["path"], request.mode.value, result["size"])
    return WriteFileResponse(**result)


@app.post("/read_file", response_model=ReadFileResponse)
async def read_file_endpoint(
    request: ReadFileRequest,
    api_key: str = Depends(verify_api_key),
):
    rate_limiter.check()

    # read_file is allowed in all modes
    result = read_file(request.path, request.encoding)
    log_file_operation("read_file", result["path"], request.mode.value, result["size"])
    return ReadFileResponse(**result)


@app.post("/delete_file", response_model=DeleteFileResponse)
async def delete_file_endpoint(
    request: DeleteFileRequest,
    api_key: str = Depends(verify_api_key),
):
    rate_limiter.check()

    # read-only mode cannot delete
    if request.mode == Mode.READ_ONLY:
        raise HTTPException(status_code=403, detail="delete_file not allowed in read-only mode")

    result = delete_file(request.path)
    log_file_operation("delete_file", result["path"], request.mode.value)
    return DeleteFileResponse(**result)
