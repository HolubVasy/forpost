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
