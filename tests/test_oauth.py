"""Tests for optional Google-OAuth bearer verification (app/oauth.py) and the
/mcp guard's accept-key-or-bearer logic."""

import asyncio

import app.oauth as oauth
from app import main


class TestAllowlist:
    def test_exact_match(self, monkeypatch):
        monkeypatch.setenv("OAUTH_ALLOWED_EMAILS", "a@x.com, b@y.com")
        assert oauth._email_allowed("A@X.com")
        assert not oauth._email_allowed("c@z.com")

    def test_domain_rule(self, monkeypatch):
        monkeypatch.setenv("OAUTH_ALLOWED_EMAILS", "@company.com")
        assert oauth._email_allowed("anyone@company.com")
        assert not oauth._email_allowed("x@other.com")


class TestEnabled:
    def test_toggle(self, monkeypatch):
        monkeypatch.setenv("OAUTH_ENABLED", "true")
        assert oauth.oauth_enabled()
        monkeypatch.setenv("OAUTH_ENABLED", "0")
        assert not oauth.oauth_enabled()

    def test_verify_disabled_returns_none(self, monkeypatch):
        monkeypatch.delenv("OAUTH_ENABLED", raising=False)
        assert oauth.verify_bearer("tok") is None  # no network call when disabled


class TestGuardAuth:
    def test_accepts_api_key(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "secret")
        headers = {b"x-api-key": b"secret"}
        assert asyncio.run(main.MCPApiKeyGuard._authenticated(headers)) is True

    def test_accepts_valid_bearer_when_oauth_on(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "k")
        monkeypatch.setattr(main, "oauth_enabled", lambda: True)
        monkeypatch.setattr(main, "verify_bearer", lambda t: "me@x.com")
        headers = {b"authorization": b"Bearer xyz"}
        assert asyncio.run(main.MCPApiKeyGuard._authenticated(headers)) is True

    def test_rejects_bad_bearer(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "k")
        monkeypatch.setattr(main, "oauth_enabled", lambda: True)
        monkeypatch.setattr(main, "verify_bearer", lambda t: None)
        headers = {b"authorization": b"Bearer bad"}
        assert asyncio.run(main.MCPApiKeyGuard._authenticated(headers)) is False

    def test_rejects_when_nothing(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "k")
        monkeypatch.setattr(main, "oauth_enabled", lambda: False)
        assert asyncio.run(main.MCPApiKeyGuard._authenticated({})) is False
