"""Tests for rate limiting."""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rate_limit import rate_limiter


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    """Configure env for all tests."""
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("WHITELIST_PATH", str(tmp_path / "whitelist.config"))
    wl = tmp_path / "whitelist.config"
    wl.write_text("git pull\n")
    # Reset rate limiter state between tests
    rate_limiter._requests.clear()


@pytest.fixture
def client():
    return TestClient(app)


HEADERS = {"X-API-Key": "test-key"}


class TestRateLimit:
    def test_requests_within_limit_succeed(self, client, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
        for _ in range(5):
            resp = client.post(
                "/execute",
                json={"command": "echo ok", "mode": "full-access"},
                headers=HEADERS,
            )
            assert resp.status_code == 200

    def test_exceeding_limit_returns_429(self, client, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
        for _ in range(3):
            resp = client.post(
                "/execute",
                json={"command": "echo ok", "mode": "full-access"},
                headers=HEADERS,
            )
            assert resp.status_code == 200

        # 4th request should be rejected
        resp = client.post(
            "/execute",
            json={"command": "echo blocked", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 429
        assert "Rate limit" in resp.json()["detail"]

    def test_old_requests_expire_from_window(self, client, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
        # Inject old timestamps (>60s ago) into the limiter
        old_time = time.time() - 61
        rate_limiter._requests = [old_time, old_time]

        # These old entries should be pruned, so new request succeeds
        resp = client.post(
            "/execute",
            json={"command": "echo ok", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 200

    def test_default_limit_is_60(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_PER_MINUTE", raising=False)
        assert rate_limiter.limit == 60

    def test_rate_limit_applies_after_auth(self, client, monkeypatch):
        """Invalid API key returns 401, not 429, even if limit exceeded."""
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
        # Exhaust limit
        client.post(
            "/execute",
            json={"command": "echo ok", "mode": "full-access"},
            headers=HEADERS,
        )
        # Bad key should still get 401, not 429
        resp = client.post(
            "/execute",
            json={"command": "echo ok", "mode": "full-access"},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401
