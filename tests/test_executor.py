"""Tests for app/executor.py - subprocess execution."""

import pytest

from app.executor import execute


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
        # Use python to sleep - cross-platform
        result = execute("py -c \"import time; time.sleep(10)\"", timeout=1)
        assert result.exit_code == -1

    def test_timeout_returns_partial_stdout(self):
        # Print then sleep - stdout captured before timeout
        result = execute("py -c \"import time; print('before_timeout'); time.sleep(10)\"", timeout=1)
        assert result.exit_code == -1
        assert "before_timeout" in result.stdout

    def test_fast_command_not_affected_by_timeout(self):
        result = execute("echo fast", timeout=5)
        assert result.exit_code == 0
        assert "fast" in result.stdout
