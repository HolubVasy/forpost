"""Tests for the shared service layer (app/service.py) and ForPostError."""

import pytest

from app import service
from app.errors import ForPostError
from app.file_ops import FileEncoding
from app.modes import Mode


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("WHITELIST_PATH", str(tmp_path / "wl.config"))
    (tmp_path / "wl.config").write_text("git pull\n")
    (tmp_path / "ws").mkdir()
    return tmp_path


class TestRunCommand:
    def test_ok(self):
        r = service.run_command("echo hi", Mode.FULL_ACCESS)
        assert "hi" in r["stdout"] and r["exit_code"] == 0 and r["timed_out"] is False

    def test_read_only_rejects_write(self):
        with pytest.raises(ForPostError) as e:
            service.run_command("rm -rf /tmp/x", Mode.READ_ONLY)
        assert e.value.status_code == 403

    def test_rejects_shell_chaining(self):
        with pytest.raises(ForPostError):
            service.run_command("ls; rm -rf x", Mode.READ_ONLY)


class TestFiles:
    def test_write_read_roundtrip(self):
        service.write("f.txt", "data", FileEncoding.TEXT, Mode.FULL_ACCESS)
        r = service.read("f.txt", FileEncoding.TEXT, Mode.READ_ONLY)
        assert r["content"] == "data"

    def test_write_rejected_in_read_only(self):
        with pytest.raises(ForPostError) as e:
            service.write("f.txt", "x", FileEncoding.TEXT, Mode.READ_ONLY)
        assert e.value.status_code == 403

    def test_read_missing_is_404(self):
        with pytest.raises(ForPostError) as e:
            service.read("nope.txt", FileEncoding.TEXT, Mode.READ_ONLY)
        assert e.value.status_code == 404


class TestJobsAndClamp:
    def test_unknown_job_is_404(self):
        with pytest.raises(ForPostError) as e:
            service.get_job_status("nope")
        assert e.value.status_code == 404
        with pytest.raises(ForPostError):
            service.kill_job("nope")

    def test_clamp_timeout(self):
        assert service.clamp_timeout(30) == 30
        assert service.clamp_timeout(0) == 1
        assert service.clamp_timeout(10_000_000) == 1800  # MAX_TIMEOUT_SECONDS


class TestDiagnostics:
    def test_snapshot_shape(self):
        d = service.diagnostics()
        assert isinstance(d["ok"], bool)
        # workspace + logs point at the tmp dirs from the fixture -> writable
        assert d["checks"]["workspace"]["ok"] is True
        assert d["checks"]["logs"]["ok"] is True
        for key in ("uptime_seconds", "memory", "jobs", "recent", "versions"):
            assert key in d
        assert d["jobs"]["running"] == 0 and d["jobs"]["zombies"] == 0
        assert "commands" in d["recent"]

    def test_reset_jobs_scope(self):
        result = service.reset("jobs")
        assert result["scope"] == "jobs" and isinstance(result["jobs_cleared"], int)

    def test_reset_logs_scope(self, env):
        (env / "commands.jsonl").write_text('{"x":1}\n')
        result = service.reset("logs")
        assert "commands.jsonl" in result["logs_cleared"]
        assert (env / "commands.jsonl").read_text() == ""

    def test_reset_all_scope(self):
        result = service.reset("all")
        assert "jobs_cleared" in result
        assert "logs_cleared" in result
        assert "workspace_cleared" in result

    def test_reset_workspace(self, env):
        (env / "ws" / "scratch.txt").write_text("x")
        result = service.reset("workspace")
        assert result["workspace_cleared"] >= 1
        assert not (env / "ws" / "scratch.txt").exists()

    def test_reset_bad_scope(self):
        with pytest.raises(ForPostError) as e:
            service.reset("bogus")
        assert e.value.status_code == 400
