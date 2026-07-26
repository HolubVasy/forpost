"""Application service layer - the single place a request is orchestrated.

Both the REST endpoints (main.py) and the MCP tools (mcp_server.py) are thin
adapters over these functions: validation, execution, jobs, file ops, timeout
clamping, and logging live here once, so the two surfaces cannot drift.

Cross-cutting transport concerns (auth, rate limiting) stay in the transports;
domain failures are raised as ForPostError and translated there.
"""

import json
import os
import time
from pathlib import Path

from app.errors import ForPostError
from app.executor import execute
from app.file_ops import FileEncoding, _get_workspace_root, delete_file, read_file, write_file
from app.jobs import job_manager
from app.logging_config import log_command, log_file_operation
from app.modes import Mode, validate_command

DEFAULT_TIMEOUT = 300
DEFAULT_JOB_TIMEOUT = 1800
_STARTED = time.time()


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
    # full-access only: allowing writes in whitelist mode + a whitelisted
    # `docker compose up` would let a crafted compose file escalate to the host.
    if mode != Mode.FULL_ACCESS:
        raise ForPostError(403, "write_file requires full-access mode")
    result = write_file(path, content, encoding)
    log_file_operation("write_file", result["path"], mode.value, result["size"])
    return result


def read(
    path: str,
    encoding: FileEncoding,
    mode: Mode,
    offset: int = 0,
    length: int | None = None,
) -> dict:
    result = read_file(path, encoding, offset, length)
    log_file_operation("read_file", result["path"], mode.value, result["size"])
    return result


def delete(path: str, mode: Mode) -> dict:
    if mode != Mode.FULL_ACCESS:
        raise ForPostError(403, "delete_file requires full-access mode")
    result = delete_file(path)
    log_file_operation("delete_file", result["path"], mode.value)
    return result


def _clear_logs() -> list:
    """Truncate ForPost's own logs (commands.jsonl + app.log)."""
    logs_dir = Path(os.environ.get("LOGS_DIR", "logs"))
    cleared = []
    for name in ("commands.jsonl", "app.log"):
        p = logs_dir / name
        try:
            if p.exists():
                open(p, "w").close()  # truncate to zero
                cleared.append(name)
        except OSError:
            pass
    return cleared


def reset(scope: str = "jobs") -> dict:
    """Reset ForPost's own state (never touches the host).

    scope:
      - "jobs" : kill all running jobs + wipe job state and job logs
      - "logs" : truncate the command + app logs
      - "all"  : both of the above
    """
    if scope not in ("jobs", "logs", "all"):
        raise ForPostError(400, "scope must be one of: jobs, logs, all")
    result = {"scope": scope}
    if scope in ("jobs", "all"):
        result["jobs_cleared"] = job_manager.purge()
    if scope in ("logs", "all"):
        result["logs_cleared"] = _clear_logs()
    return result


def _writable(path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".forpost_write_probe"
        probe.write_text("ok")
        probe.unlink()
        return {"ok": True, "path": str(path)}
    except OSError as exc:
        return {"ok": False, "path": str(path), "detail": str(exc)}


def _process_rss_mb():
    """Resident memory of this process, MB - to spot a leak/bloat."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        import resource  # Unix; ru_maxrss is KB on Linux

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        return None


def _system_mem():
    """System memory (available/total, MB) - is the box out of memory?"""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key.strip()] = rest.strip()

        def mb(key):
            parts = info.get(key, "").split()
            return round(int(parts[0]) / 1024, 1) if parts else None

        return {"available_mb": mb("MemAvailable"), "total_mb": mb("MemTotal")}
    except OSError:
        return {"available_mb": None, "total_mb": None}


def _tail_lines(path: Path, max_lines: int = 50, max_bytes: int = 65536) -> list:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read()
        return data.decode("utf-8", errors="replace").splitlines()[-max_lines:]
    except OSError:
        return []


def _recent_commands(logs_dir: Path) -> dict:
    """Summarise the last commands so you don't have to open the log."""
    scanned = 0
    failures = 0
    last_failure = None
    for line in _tail_lines(logs_dir / "commands.jsonl", 50):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        scanned += 1
        code = entry.get("exit_code")
        if code not in (0, None):
            failures += 1
            last_failure = {
                "command": entry.get("command"),
                "exit_code": code,
                "timestamp": entry.get("timestamp"),
            }
    return {"scanned": scanned, "failures": failures, "last_failure": last_failure}


def _last_app_issue(logs_dir: Path):
    for line in reversed(_tail_lines(logs_dir / "app.log", 100)):
        if "WARNING" in line or "ERROR" in line:
            return line[:200]
    return None


def diagnostics() -> dict:
    """One-call health snapshot: alive?, memory, jobs (running / stuck), disk,
    tool versions, recent failures. A fixed, safe set of probes - no mode/
    arbitrary command. Read this instead of digging through the logs."""
    checks = {}
    dv = execute("docker version", timeout=10)
    detail = (dv.stdout or dv.stderr).strip()
    checks["docker"] = {
        "ok": dv.exit_code == 0,
        "detail": detail.splitlines()[0][:200] if detail else "",
    }
    checks["workspace"] = _writable(_get_workspace_root())
    checks["logs"] = _writable(Path(os.environ.get("LOGS_DIR", "logs")))

    versions = {}
    for name, cmd in (
        ("git", "git --version"),
        ("gh", "gh --version"),
        ("docker", "docker --version"),
        ("python", "python3 --version"),
    ):
        r = execute(cmd, timeout=10)
        versions[name] = (
            r.stdout.strip().splitlines()[0]
            if (r.exit_code == 0 and r.stdout.strip())
            else None
        )

    logs_dir = Path(os.environ.get("LOGS_DIR", "logs"))
    try:
        cj = logs_dir / "commands.jsonl"
        commands_log_bytes = cj.stat().st_size if cj.exists() else 0
    except OSError:
        commands_log_bytes = 0

    jobs = job_manager.snapshot()
    healthy = all(c["ok"] for c in checks.values()) and jobs["zombies"] == 0

    return {
        "ok": healthy,
        "uptime_seconds": round(time.time() - _STARTED, 1),
        "checks": checks,
        "memory": {
            "process_rss_mb": _process_rss_mb(),
            "system": _system_mem(),
        },
        "jobs": jobs,
        "recent": {
            "commands": _recent_commands(logs_dir),
            "last_app_issue": _last_app_issue(logs_dir),
        },
        "versions": versions,
        "commands_log_bytes": commands_log_bytes,
    }
