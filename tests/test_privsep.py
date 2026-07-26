"""Privilege separation: read-only/whitelist commands run as an unprivileged
user, git tokens are scoped per mode, and /mcp can be made OAuth-only.

These are the guarantees that make read-only a real confidentiality boundary
(it can't read the app's env/key) and keep the work repo read-only (its token
is only ever exposed in whitelist mode, which allows read commands only)."""

import asyncio

import app.executor as executor
from app import main
from app.executor import build_popen_args, child_env
from app.modes import Mode
from app.service import _privilege


class TestChildEnvTokenScope:
    def test_api_key_always_stripped(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "secret")
        for scope in (None, "personal", "work"):
            assert "API_KEY" not in child_env(scope)

    def test_read_only_gets_no_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "personal-tok")
        monkeypatch.setenv("GITHUB_WORK_TOKEN", "work-tok")
        env = child_env(None)
        assert "GITHUB_TOKEN" not in env
        assert "GH_TOKEN" not in env
        assert "GITHUB_WORK_TOKEN" not in env

    def test_personal_scope_uses_personal_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "personal-tok")
        monkeypatch.setenv("GITHUB_WORK_TOKEN", "work-tok")
        env = child_env("personal")
        assert env["GITHUB_TOKEN"] == "personal-tok"
        assert env["GH_TOKEN"] == "personal-tok"
        # the work token is never exposed to full-access - can't write work repo
        assert "GITHUB_WORK_TOKEN" not in env

    def test_work_scope_uses_work_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "personal-tok")
        monkeypatch.setenv("GITHUB_WORK_TOKEN", "work-tok")
        env = child_env("work")
        assert env["GITHUB_TOKEN"] == "work-tok"
        assert env["GH_TOKEN"] == "work-tok"


class TestBuildPopenArgs:
    def test_unrestricted_runs_in_process(self, monkeypatch):
        monkeypatch.setenv("RESTRICTED_COMMAND_USER", "forpost-ro")
        popen = build_popen_args("ls -la", restricted=False, git_token=None)
        assert popen["shell"] is True
        assert popen["args"] == "ls -la"

    def test_restricted_wraps_with_sudo(self, monkeypatch):
        monkeypatch.setenv("RESTRICTED_COMMAND_USER", "forpost-ro")
        monkeypatch.setattr(executor, "_NEW_SESSION", True)
        monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/sudo")
        popen = build_popen_args("git status", restricted=True, git_token="work")
        assert popen["shell"] is False
        assert popen["args"][:6] == ["sudo", "-n", "-H", "-u", "forpost-ro", "bash"]
        assert popen["args"][-1] == "git status"

    def test_restricted_without_user_runs_in_process(self, monkeypatch):
        monkeypatch.delenv("RESTRICTED_COMMAND_USER", raising=False)
        popen = build_popen_args("ls", restricted=True, git_token=None)
        assert popen["shell"] is True


class TestPrivilegeMapping:
    def test_read_only(self):
        assert _privilege(Mode.READ_ONLY) == (True, None)

    def test_whitelist_is_work_readonly(self):
        assert _privilege(Mode.WHITELIST) == (True, "work")

    def test_full_access_is_personal(self):
        assert _privilege(Mode.FULL_ACCESS) == (False, "personal")


class TestMcpRequireOauth:
    def test_key_rejected_when_oauth_only(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "k")
        monkeypatch.setenv("MCP_REQUIRE_OAUTH", "true")
        monkeypatch.setattr(main, "oauth_enabled", lambda: True)
        monkeypatch.setattr(main, "verify_bearer", lambda t: None)
        headers = {b"x-api-key": b"k"}
        assert asyncio.run(main.MCPApiKeyGuard._authenticated(headers)) is False

    def test_key_accepted_when_not_oauth_only(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "k")
        monkeypatch.delenv("MCP_REQUIRE_OAUTH", raising=False)
        headers = {b"x-api-key": b"k"}
        assert asyncio.run(main.MCPApiKeyGuard._authenticated(headers)) is True

    def test_bearer_still_accepted_when_oauth_only(self, monkeypatch):
        monkeypatch.setenv("MCP_REQUIRE_OAUTH", "true")
        monkeypatch.setattr(main, "oauth_enabled", lambda: True)
        monkeypatch.setattr(main, "verify_bearer", lambda t: "me@x.com")
        headers = {b"authorization": b"Bearer xyz"}
        assert asyncio.run(main.MCPApiKeyGuard._authenticated(headers)) is True
