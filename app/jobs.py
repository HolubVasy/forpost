"""Background job execution for long-running commands (the async model).

Minimal by design: hand the command to the shell, don't let it hang, be able
to kill it, and don't let parallel jobs trip over shared state or pile up.

  - `start(command, timeout)` spawns the process (own process-group, own log
    file) and returns a job_id at once.
  - `status(job_id, offset)` returns output from `offset` onward - poll to
    follow progress / paginate; reports status + exit_code when finished.
  - `kill(job_id)` force-kills the whole process group.

Lifecycle / cleanup (so nothing dangles):
  - concurrency is capped (MAX_CONCURRENT_JOBS) so jobs can't pile up;
  - finished jobs (and their log files) are reaped after JOB_RETENTION_SECONDS;
  - `shutdown()` kills everything still running (a container restart = clean
    slate); `purge()` is the explicit "kill all + wipe state + delete logs".

The child writes straight to its log file (own fd), so job output never buffers
in memory. Per-job disk is capped at MAX_JOB_LOG_BYTES; job state is in-memory
and does not survive a restart (the log files may, until reaped).
"""

import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.errors import ForPostError
from app.executor import _NEW_SESSION, build_popen_args, kill_process_tree

_TERMINAL = {"done", "timeout", "killed", "error"}


def _jobs_dir() -> Path:
    d = Path(os.environ.get("LOGS_DIR", "logs")) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _max_job_log_bytes() -> int:
    return int(os.environ.get("MAX_JOB_LOG_BYTES", str(50 * 1024 * 1024)))


def _status_chunk_limit() -> int:
    return int(os.environ.get("MAX_OUTPUT_CHARS", "100000"))


def _max_concurrent() -> int:
    return int(os.environ.get("MAX_CONCURRENT_JOBS", "5"))


def _retention_seconds() -> int:
    return int(os.environ.get("JOB_RETENTION_SECONDS", "3600"))


@dataclass
class Job:
    id: str
    command: str
    log_path: str
    status: str = "running"  # running | done | timeout | killed | error
    exit_code: int | None = None
    truncated: bool = False
    finished_at: float | None = None


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # --- lifecycle -------------------------------------------------------

    def start(
        self,
        command: str,
        timeout: int,
        restricted: bool = False,
        git_token: str | None = None,
    ) -> str:
        with self._lock:
            self._reap_locked(time.time())
            running = sum(1 for j in self._jobs.values() if j.status == "running")
            if running >= _max_concurrent():
                raise ForPostError(
                    503, f"too many concurrent jobs (max {_max_concurrent()})"
                )

        job_id = uuid.uuid4().hex[:16]
        log_path = _jobs_dir() / f"{job_id}.log"
        job = Job(id=job_id, command=command, log_path=str(log_path))
        logf = open(log_path, "wb")
        popen = build_popen_args(command, restricted, git_token)
        proc = subprocess.Popen(
            popen["args"],
            shell=popen["shell"],
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=_NEW_SESSION,
            env=popen["env"],
        )
        with self._lock:
            self._jobs[job_id] = job
            self._procs[job_id] = proc
        threading.Thread(
            target=self._supervise, args=(job, proc, logf, timeout), daemon=True
        ).start()
        return job_id

    def _finalize(self, job: Job, status: str, exit_code: int | None) -> None:
        job.status = status
        job.exit_code = exit_code
        job.finished_at = time.time()

    def _supervise(self, job: Job, proc: subprocess.Popen, logf, timeout: int) -> None:
        deadline = time.time() + timeout if timeout else None
        cap = _max_job_log_bytes()
        try:
            while True:
                try:
                    proc.wait(timeout=1)
                    break  # finished (naturally or via kill)
                except subprocess.TimeoutExpired:
                    if deadline and time.time() > deadline:
                        kill_process_tree(proc)
                        proc.wait()
                        self._finalize(job, "timeout", -1)
                        return
                    try:
                        if os.path.getsize(job.log_path) > cap:
                            kill_process_tree(proc)
                            proc.wait()
                            job.truncated = True
                            self._finalize(job, "killed", -1)
                            return
                    except OSError:
                        pass
            if job.status == "running":  # not already killed
                self._finalize(job, "done", proc.returncode)
        except Exception:  # pragma: no cover - defensive
            self._finalize(job, "error", None)
        finally:
            try:
                logf.close()
            except Exception:
                pass

    # --- queries ---------------------------------------------------------

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "running")

    def snapshot(self) -> dict:
        """Counts by status + a zombie count (marked running but the process
        actually died) - for the diagnostics 'alive / stuck' picture."""
        with self._lock:
            by_status: dict[str, int] = {}
            zombies = 0
            for jid, job in self._jobs.items():
                by_status[job.status] = by_status.get(job.status, 0) + 1
                proc = self._procs.get(jid)
                if job.status == "running" and proc is not None and proc.poll() is not None:
                    zombies += 1
            return {
                "total": len(self._jobs),
                "running": by_status.get("running", 0),
                "by_status": by_status,
                "zombies": zombies,
            }

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
            kill_process_tree(proc)
            self._finalize(job, "killed", -1)
        return {"job_id": job_id, "status": job.status}

    # --- cleanup ---------------------------------------------------------

    def _reap_locked(self, now: float) -> None:
        """Drop finished jobs (and their logs) older than the retention window.
        Caller must hold the lock."""
        retention = _retention_seconds()
        stale = [
            jid
            for jid, j in self._jobs.items()
            if j.finished_at is not None and now - j.finished_at > retention
        ]
        for jid in stale:
            job = self._jobs.pop(jid, None)
            self._procs.pop(jid, None)
            if job:
                try:
                    os.remove(job.log_path)
                except OSError:
                    pass

    def shutdown(self) -> None:
        """Kill every still-running job (called on app shutdown)."""
        with self._lock:
            procs = list(self._procs.values())
            for job in self._jobs.values():
                if job.status == "running":
                    job.status = "killed"
        for proc in procs:
            if proc.poll() is None:
                kill_process_tree(proc)

    def purge(self) -> int:
        """Kill all running jobs and wipe ALL job state + log files (including
        orphaned logs left on disk, e.g. after a restart). Returns count."""
        with self._lock:
            jobs = list(self._jobs.values())
            procs = list(self._procs.values())
            self._jobs.clear()
            self._procs.clear()
        for proc in procs:
            if proc.poll() is None:
                kill_process_tree(proc)
        # Sweep every job log file, not just the ones still tracked in memory.
        try:
            for f in _jobs_dir().glob("*.log"):
                try:
                    f.unlink()
                except OSError:
                    pass
        except OSError:
            pass
        return len(jobs)


job_manager = JobManager()
