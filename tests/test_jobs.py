"""Tests for the background job model (app/jobs.py)."""

import sys
import time

import pytest

from app.errors import ForPostError
from app.jobs import JobManager

PY = f'"{sys.executable}"'


@pytest.fixture(autouse=True)
def logs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    return tmp_path


def _wait_done(mgr, job_id, timeout=15):
    deadline = time.time() + timeout
    st = mgr.status(job_id)
    while time.time() < deadline:
        st = mgr.status(job_id)
        if not st["running"]:
            return st
        time.sleep(0.2)
    raise AssertionError(f"job did not finish: {st}")


class TestJobs:
    def test_job_runs_and_captures_output(self):
        mgr = JobManager()
        job_id = mgr.start(f"{PY} -c \"print('hello_job')\"", timeout=30)
        st = _wait_done(mgr, job_id)
        assert st["status"] == "done"
        assert st["exit_code"] == 0
        assert "hello_job" in st["output"]

    def test_status_offset_paginates(self):
        mgr = JobManager()
        job_id = mgr.start(f"{PY} -c \"print('A'*100)\"", timeout=30)
        _wait_done(mgr, job_id)
        full = mgr.status(job_id, offset=0)
        assert full["total_bytes"] >= 100
        part = mgr.status(job_id, offset=50)
        assert part["offset"] == 50
        assert len(part["output"]) < len(full["output"])
        assert part["next_offset"] <= full["total_bytes"]

    def test_timeout_marks_job(self):
        mgr = JobManager()
        job_id = mgr.start(f'{PY} -c "import time; time.sleep(10)"', timeout=1)
        st = _wait_done(mgr, job_id, timeout=10)
        assert st["status"] == "timeout"
        assert st["exit_code"] == -1

    def test_kill_stops_job(self):
        mgr = JobManager()
        job_id = mgr.start(f'{PY} -c "import time; time.sleep(30)"', timeout=60)
        time.sleep(1)
        res = mgr.kill(job_id)
        assert res["status"] == "killed"
        st = _wait_done(mgr, job_id, timeout=10)
        assert st["status"] == "killed"

    def test_unknown_job_raises(self):
        mgr = JobManager()
        with pytest.raises(KeyError):
            mgr.status("nope")
        with pytest.raises(KeyError):
            mgr.kill("nope")


class TestJobLifecycle:
    def test_concurrency_cap(self, monkeypatch):
        monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
        mgr = JobManager()
        j1 = mgr.start(f'{PY} -c "import time; time.sleep(5)"', timeout=30)
        with pytest.raises(ForPostError) as e:
            mgr.start(f"{PY} -c \"print(1)\"", timeout=30)
        assert e.value.status_code == 503
        mgr.kill(j1)

    def test_reap_removes_finished(self, monkeypatch):
        monkeypatch.setenv("JOB_RETENTION_SECONDS", "0")
        mgr = JobManager()
        j = mgr.start(f"{PY} -c \"print('x')\"", timeout=30)
        _wait_done(mgr, j)
        time.sleep(0.05)
        # starting another reaps the finished one (retention 0)
        mgr.start(f"{PY} -c \"print('y')\"", timeout=30)
        with pytest.raises(KeyError):
            mgr.status(j)

    def test_purge_kills_and_wipes(self):
        mgr = JobManager()
        j = mgr.start(f'{PY} -c "import time; time.sleep(5)"', timeout=30)
        count = mgr.purge()
        assert count >= 1
        with pytest.raises(KeyError):
            mgr.status(j)

    def test_shutdown_kills_running(self):
        mgr = JobManager()
        j = mgr.start(f'{PY} -c "import time; time.sleep(5)"', timeout=30)
        mgr.shutdown()
        st = mgr.status(j)
        assert st["status"] == "killed"
