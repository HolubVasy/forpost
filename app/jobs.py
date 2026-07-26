"""Background job execution for long-running commands (the async model).

`execute()` is synchronous - one request, one response, bounded by a timeout.
For tasks that outlive a single request (e.g. `docker build`), run them as a
background job instead:

  - `start(command, timeout)` spawns the process and returns a job_id at once.
  - `status(job_id, offset)` returns the output produced so far from `offset`
    onward - poll it to *follow progress*, and use `offset`/`next_offset` to
    paginate. Reports status + exit_code when finished.
  - `kill(job_id)` terminates a running job.

The child writes stdout+stderr straight to a per-job log file (its own fd), so
job output never buffers in ForPost's memory. Disk per job is capped at
MAX_JOB_LOG_BYTES; the timeout bounds wall-clock time. Job state is in-memory
and does not survive a container restart (the log files do).
"""

import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.executor import kill_process_tree, _NEW_SESSION


def _jobs_dir() -> Path:
    d = Path(os.environ.get("LOGS_DIR", "logs")) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _max_job_log_bytes() -> int:
    return int(os.environ.get("MAX_JOB_LOG_BYTES", str(50 * 1024 * 1024)))


def _status_chunk_limit() -> int:
    return int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))


@dataclass
class Job:
    id: str
    command: str
    log_path: str
    status: str = "running"  # running | done | timeout | killed | error
    exit_code: int | None = None
    truncated: bool = False


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def start(self, command: str, timeout: int) -> str:
        job_id = uuid.uuid4().hex[:16]
        log_path = _jobs_dir() / f"{job_id}.log"
        job = Job(id=job_id, command=command, log_path=str(log_path))
        logf = open(log_path, "wb")
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=_NEW_SESSION,
        )
        with self._lock:
            self._jobs[job_id] = job
            self._procs[job_id] = proc
        threading.Thread(
            target=self._supervise, args=(job, proc, logf, timeout), daemon=True
        ).start()
        return job_id

    def _supervise(self, job: Job, proc: subprocess.Popen, logf, timeout: int) -> None:
        deadline = time.time() + timeout if timeout else None
        cap = _max_job_log_bytes()
        try:
            while True:
                try:
                    proc.wait(timeout=1)
                    break  # process finished (naturally or via kill)
                except subprocess.TimeoutExpired:
                    if deadline and time.time() > deadline:
                        kill_process_tree(proc)
                        proc.wait()
                        job.status = "timeout"
                        job.exit_code = -1
                        return
                    try:
                        if os.path.getsize(job.log_path) > cap:
                            kill_process_tree(proc)
                            proc.wait()
                            job.truncated = True
                            job.status = "killed"
                            job.exit_code = -1
                            return
                    except OSError:
                        pass
            # Finished. If kill() already marked it, leave that status.
            if job.status == "running":
                job.exit_code = proc.returncode
                job.status = "done"
        except Exception:  # pragma: no cover - defensive
            job.status = "error"
        finally:
            try:
                logf.close()
            except Exception:
                pass

    def status(self, job_id: str, offset: int = 0) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        limit = _status_chunk_limit()
        chunk = b""
        total = 0
        try:
            total = os.path.getsize(job.log_path)
            with open(job.log_path, "rb") as f:
                f.seek(max(0, int(offset)))
                chunk = f.read(limit)
        except FileNotFoundError:
            pass
        return {
            "job_id": job_id,
            "status": job.status,
            "running": job.status == "running",
            "exit_code": job.exit_code,
            "output": chunk.decode("utf-8", errors="replace"),
            "offset": int(offset),
            "next_offset": int(offset) + len(chunk),
            "total_bytes": total,
            "truncated": job.truncated,
        }

    def kill(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            proc = self._procs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if proc is not None and proc.poll() is None:
            job.status = "killed"
            job.exit_code = -1
            kill_process_tree(proc)
        return {"job_id": job_id, "status": job.status}


job_manager = JobManager()
