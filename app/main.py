"""ForPost - main FastAPI application."""

import json
import os
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
from app.oauth import oauth_enabled, verify_bearer
from app.rate_limit import rate_limiter

# Streamable-HTTP ASGI app exposing the MCP tools (execute_command + file ops).
# Mounted at /mcp below so Claude custom connectors can reach it at
# https://<host>/mcp, sharing the exact same logic as the REST endpoints.
mcp_app = mcp.streamable_http_app()


async def _send_json_error(send, status: int, message: str, extra_headers=None) -> None:
    body = json.dumps({"error": message}).encode()
    headers = [(b"content-type", b"application/json")]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _www_authenticate_headers():
    """On 401, point an MCP client at our protected-resource metadata (RFC 9728)
    so it can discover the OAuth server. Only when OAuth is enabled."""
    if not oauth_enabled():
        return None
    base = os.environ.get("PUBLIC_URL", "https://forpost.poly-mind.net").rstrip("/")
    meta = f"{base}/.well-known/oauth-protected-resource"
    return [(b"www-authenticate", f'Bearer resource_metadata="{meta}"'.encode())]


class MCPApiKeyGuard:
    """Guards the mounted /mcp app: accept a static X-API-Key OR (when OAuth is
    enabled) a verified Google Bearer token, then rate-limit. The mounted MCP
    sub-app bypasses the REST dependency + rate-limit, so this re-applies both.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if not await self._authenticated(headers):
                get_app_logger().warning("MCP request rejected: missing/invalid credentials")
                await _send_json_error(
                    send, 401, "Missing or invalid credentials",
                    extra_headers=_www_authenticate_headers(),
                )
                return
            try:
                rate_limiter.check()
            except HTTPException as exc:
                await _send_json_error(send, exc.status_code, str(exc.detail))
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _authenticated(headers: dict) -> bool:
        # 1) static X-API-Key - primary model (also used by REST/tests/curl).
        provided = headers.get(b"x-api-key", b"").decode()
        try:
            expected = get_api_key()
        except RuntimeError:
            expected = None
        if expected and keys_match(provided, expected):
            return True
        # 2) Google OAuth bearer, only when enabled. verify_bearer blocks on a
        #    network call to Google, so run it off the event loop.
        if oauth_enabled():
            auth = headers.get(b"authorization", b"").decode()
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
                if await run_in_threadpool(verify_bearer, token):
                    return True
        return False


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
# In OAuth mode the mounted MCP app enforces Google Bearer auth itself, so the
# guard only rate-limits (path_auth=True). Otherwise it enforces X-API-Key.
app.mount("/mcp", MCPApiKeyGuard(mcp_app))


if oauth_enabled():
    _RESOURCE = os.environ.get("PUBLIC_URL", "https://forpost.poly-mind.net").rstrip("/") + "/mcp"
    _PR_METADATA = {
        "resource": _RESOURCE,
        "authorization_servers": ["https://accounts.google.com"],
        "scopes_supported": ["openid", "email", "profile"],
        "bearer_methods_supported": ["header"],
    }

    # RFC 9728 protected-resource metadata, served at the domain ROOT (where
    # the 401 WWW-Authenticate points), advertising Google as the auth server.
    @app.get("/.well-known/oauth-protected-resource/mcp")
    @app.get("/.well-known/oauth-protected-resource")
    async def oauth_protected_resource():
        return _PR_METADATA

logger = get_app_logger()


@app.exception_handler(ForPostError)
async def _forpost_error_handler(request: Request, exc: ForPostError):
    """Central translation of domain errors to HTTP responses."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health")
async def health():
    """Unauthenticated liveness check for container healthchecks / proxies."""
    return {"status": "ok"}


@app.get("/diagnostics")
async def diagnostics_endpoint(api_key: str = Depends(verify_api_key)):
    """Full health snapshot (authenticated). Same data as the MCP diagnostics tool."""
    return await run_in_threadpool(service.diagnostics)


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


class ResetRequest(BaseModel):
    scope: str = "jobs"  # "jobs" | "logs" | "all"


@app.post("/reset")
async def reset_endpoint(
    request: ResetRequest,
    api_key: str = Depends(verify_api_key),
):
    """Reset ForPost's own state: scope = jobs | logs | all."""
    return await run_in_threadpool(service.reset, request.scope)
