"""ForPost - main FastAPI application."""

import json
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import service
from app.auth import get_api_key, keys_match, verify_api_key
from app.errors import ForPostError
from app.file_ops import FileEncoding
from app.jobs import job_manager
from app.logging_config import get_app_logger
from app.mcp_server import mcp
from app.modes import Mode
from app.rate_limit import rate_limiter

# Streamable-HTTP ASGI app exposing the MCP tools (execute_command + file ops).
# Mounted at /mcp below so Claude custom connectors can reach it at
# https://<host>/mcp, sharing the exact same logic as the REST endpoints.
mcp_app = mcp.streamable_http_app()


async def _send_json_error(send, status: int, message: str) -> None:
    body = json.dumps({"error": message}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})


class MCPApiKeyGuard:
    """ASGI middleware guarding the mounted /mcp app with auth + rate limiting.

    The FastAPI dependency (verify_api_key) and the /execute rate-limit only
    cover the REST routes; the mounted MCP sub-app bypasses both, so without
    this every MCP call (incl. protocol chatter, not just tool executions)
    would be unauthenticated and unthrottled. This applies the same static-key
    model and the same rate limiter to all of /mcp.
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
            if not expected or not keys_match(provided, expected):
                get_app_logger().warning("MCP request rejected: missing/invalid API key")
                await _send_json_error(send, 401, "Missing or invalid API key")
                return
            try:
                rate_limiter.check()
            except HTTPException as exc:
                await _send_json_error(send, exc.status_code, str(exc.detail))
                return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(application: FastAPI):
    get_app_logger().info("ForPost started")
    # The MCP session manager must run for the streamable-HTTP endpoint to work.
    async with mcp.session_manager.run():
        yield
    # Kill any still-running background jobs on shutdown (clean slate on restart).
    job_manager.shutdown()
    get_app_logger().info("ForPost stopped")


app = FastAPI(title="ForPost", version="0.1.0", lifespan=lifespan)
app.mount("/mcp", MCPApiKeyGuard(mcp_app))

logger = get_app_logger()


@app.exception_handler(ForPostError)
async def _forpost_error_handler(request: Request, exc: ForPostError):
    """Central translation of domain errors to HTTP responses."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


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
    rate_limiter.check()
    # Offloaded to a worker thread so a long command doesn't block the loop.
    return await run_in_threadpool(
        service.run_command, request.command, request.mode, request.timeout
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
    offset: int = 0
    length: int | None = None


class ReadFileResponse(BaseModel):
    path: str
    content: str
    size: int  # total file size in bytes
    offset: int = 0
    next_offset: int = 0
    truncated: bool = False


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
    return await run_in_threadpool(
        service.write, request.path, request.content, request.encoding, request.mode
    )


@app.post("/read_file", response_model=ReadFileResponse)
async def read_file_endpoint(
    request: ReadFileRequest,
    api_key: str = Depends(verify_api_key),
):
    # read is a bounded query - exempt from the app rate limit (nginx still caps).
    return await run_in_threadpool(
        service.read,
        request.path,
        request.encoding,
        request.mode,
        request.offset,
        request.length,
    )


@app.post("/delete_file", response_model=DeleteFileResponse)
async def delete_file_endpoint(
    request: DeleteFileRequest,
    api_key: str = Depends(verify_api_key),
):
    rate_limiter.check()
    return await run_in_threadpool(service.delete, request.path, request.mode)


# --- Background jobs (async model for long-running commands) ---


class RunAsyncRequest(BaseModel):
    command: str
    mode: Mode
    timeout: int = 1800


class JobStatusRequest(BaseModel):
    job_id: str
    offset: int = 0


class JobKillRequest(BaseModel):
    job_id: str


@app.post("/run_async")
async def run_async_endpoint(
    request: RunAsyncRequest,
    api_key: str = Depends(verify_api_key),
):
    rate_limiter.check()
    return await run_in_threadpool(
        service.start_job, request.command, request.mode, request.timeout
    )


@app.post("/job_status")
async def job_status_endpoint(
    request: JobStatusRequest,
    api_key: str = Depends(verify_api_key),
):
    # Polling a job must not be rate-limited (it would block job_kill).
    return await run_in_threadpool(
        service.get_job_status, request.job_id, request.offset
    )


@app.post("/job_kill")
async def job_kill_endpoint(
    request: JobKillRequest,
    api_key: str = Depends(verify_api_key),
):
    # Killing a job is a safety action - never rate-limited.
    return await run_in_threadpool(service.kill_job, request.job_id)
