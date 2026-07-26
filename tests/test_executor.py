"""Tests for app/executor.py - subprocess execution."""

import sys

import pytest

from app.executor import execute

PY = f'"{sys.executable}"'  # portable python invocation (Windows + Linux)


class TestExecutor:
    def test_simple_command_stdout(self):
        result = execute("echo hello")
        assert "hello" in result.stdout
        assert result.exit_code == 0

    def test_failing_command_exit_code(self):
        # 'exit 1' in a shell returns exit code 1
        result = execute("exit 1")
        assert result.exit_code == 1

    def test_stderr_captured(self):
        # Redirect a message to stderr
        result = execute("echo error_msg 1>&2")
        assert "error_msg" in result.stderr
        assert result.exit_code == 0

    def test_command_with_args(self):
        result = execute("echo one two three")
        assert "one two three" in result.stdout
        assert result.exit_code == 0

    def test_nonexistent_command(self):
        result = execute("nonexistent_command_xyz_12345")
        assert result.exit_code != 0
        # stderr should contain some error message
        assert result.stderr != ""

    def test_timeout_kills_long_running_command(self):
        result = execute(f'{PY} -c "import time; time.sleep(10)"', timeout=1)
        assert result.exit_code == -1

    def test_timeout_returns_partial_stdout(self):
        # Print (flushed) then sleep - stdout captured before the kill.
        result = execute(
            f"{PY} -c \"import time; print('before_timeout', flush=True); time.sleep(10)\"",
            timeout=2,
        )
        assert result.exit_code == -1
        assert "before_timeout" in result.stdout

    def test_fast_command_not_affected_by_timeout(self):
        result = execute("echo fast", timeout=5)
        assert result.exit_code == 0
        assert "fast" in result.stdout

    def test_output_is_capped(self, monkeypatch):
        monkeypatch.setenv("MAX_OUTPUT_CHARS", "50")
        result = execute(f'{PY} -c "print(\'a\'*500)"')
        assert result.exit_code == 0
        assert len(result.stdout) < 200  # capped well below the 500 produced
        assert "truncated" in result.stdout

    def test_child_env_strips_api_key(self, monkeypatch):
        from app.executor import child_env

        monkeypatch.setenv("API_KEY", "supersecret")
        monkeypatch.setenv("GITHUB_TOKEN", "ghtok")
        # No scope (read-only): API_KEY and the git token are both absent.
        env = child_env()
        assert "API_KEY" not in env
        assert "GITHUB_TOKEN" not in env
        # personal scope (full-access): the personal token is exposed for gh/git.
        env = child_env("personal")
        assert "API_KEY" not in env
        assert env.get("GITHUB_TOKEN") == "ghtok"

    def test_executed_command_cannot_read_api_key(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "supersecret")
        result = execute(f"{PY} -c \"import os; print(os.environ.get('API_KEY'))\"")
        assert "supersecret" not in result.stdout

    def test_large_output_is_memory_bounded(self):
        # ~2 MB of output; default cap 100k -> result stays small, tail kept,
        # and the process must not blow up memory or error.
        result = execute(f'{PY} -c "print(\'x\'*2000000)"')
        assert result.exit_code == 0
        assert len(result.stdout) < 200000
        assert "truncated" in result.stdout
        assert result.stdout.rstrip().endswith("x")  # tail preserved
