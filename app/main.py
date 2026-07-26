"""ForPost - main FastAPI application."""

from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.auth import verify_api_key
from app.executor import execute
from app.file_ops import FileEncoding, delete_file, read_file, write_file
from app.logging_config import get_app_logger, log_command, log_file_operation
from app.modes import Mode, validate_command
from app.rate_limit import rate_limiter


@asynccontextmanager
async def lifespan(application: FastAPI):
    get_app_logger().info("ForPost started")
    yield


app = FastAPI(title="ForPost", version="0.1.0", lifespan=lifespan)

logger = get_app_logger()


@app.get("/health")
async def health():
    """Unauthenticated liveness check for container healthchecks / proxies."""
    return {"status": "ok"}


class ExecuteRequest(BaseModel):
    command: str
    mode: Mode


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

    # Execute with 5-minute hard timeout
    result = execute(request.command, timeout=300)
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
