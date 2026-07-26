"""Tests for app/logging_config.py - command and app logging."""

import json
import pytest
from pathlib import Path

from app.logging_config import log_command, get_app_logger


@pytest.fixture
def logs_dir(tmp_path, monkeypatch):
    """Use a temporary directory for logs."""
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    return tmp_path


class TestCommandLogging:
    def test_log_command_creates_jsonl_entry(self, logs_dir):
        log_command(
            command="echo hello",
            mode="read-only",
            exit_code=0,
            stdout="hello\n",
            stderr="",
        )
        jsonl_file = logs_dir / "commands.jsonl"
        assert jsonl_file.exists()
        line = jsonl_file.read_text().strip()
        entry = json.loads(line)
        assert entry["command"] == "echo hello"
        assert entry["mode"] == "read-only"
        assert entry["exit_code"] == 0
        assert entry["stdout"] == "hello\n"
        assert entry["stderr"] == ""
        assert "timestamp" in entry

    def test_log_command_appends_multiple_entries(self, logs_dir):
        log_command("cmd1", "read-only", 0, "out1", "")
        log_command("cmd2", "full-access", 1, "", "err2")
        jsonl_file = logs_dir / "commands.jsonl"
        lines = jsonl_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["command"] == "cmd1"
        assert json.loads(lines[1])["command"] == "cmd2"

    def test_log_command_timestamp_is_iso_format(self, logs_dir):
        log_command("echo test", "full-access", 0, "test", "")
        jsonl_file = logs_dir / "commands.jsonl"
        entry = json.loads(jsonl_file.read_text().strip())
        # Should parse without error
        from datetime import datetime
        datetime.fromisoformat(entry["timestamp"])


class TestRedactionAndTrim:
    def test_secrets_redacted(self, logs_dir):
        log_command(
            "echo",
            "full-access",
            0,
            "key sk-or-v1-abcdefghijklmnopqrstuvwxyz1234 and ghp_ABCDEFGHIJKLMNOPQRSTUV",
            "",
        )
        content = (logs_dir / "commands.jsonl").read_text()
        assert "sk-or-v1-abcdefghijklmnopqrstuvwxyz1234" not in content
        assert "ghp_ABCDEFGHIJKLMNOPQRSTUV" not in content
        assert "REDACTED" in content

    def test_commands_log_trims_on_overflow(self, logs_dir, monkeypatch):
        monkeypatch.setenv("MAX_LOG_BYTES", "500")
        monkeypatch.setenv("LOG_TRIM_FRACTION", "0.5")
        for i in range(200):
            log_command(f"cmd{i}", "read-only", 0, "x" * 10, "")
        p = logs_dir / "commands.jsonl"
        assert p.stat().st_size < 3000  # bounded, not 200 * entry
        text = p.read_text()
        assert "cmd199" in text  # newest kept
        assert "cmd0" not in text  # oldest dropped


class TestAppLogging:
    def test_app_logger_writes_to_file(self, logs_dir):
        logger = get_app_logger()
        logger.info("ForPost started")
        app_log = logs_dir / "app.log"
        assert app_log.exists()
        content = app_log.read_text()
        assert "ForPost started" in content

    def test_app_logger_includes_level(self, logs_dir):
        logger = get_app_logger()
        logger.warning("Auth failure from unknown key")
        app_log = logs_dir / "app.log"
        content = app_log.read_text()
        assert "WARNING" in content
        assert "Auth failure" in content
