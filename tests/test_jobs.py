"""Tests for the background job model (app/jobs.py)."""

import sys
import time

import pytest

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
