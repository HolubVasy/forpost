"""Tests for the mounted MCP streamable-HTTP endpoint (/mcp) and its API-key guard.

These exercise the HTTP surface Claude custom connectors talk to, distinct from
test_mcp_server.py which calls the tools in-process.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rate_limit import rate_limiter

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("WHITELIST_PATH", str(tmp_path / "whitelist.config"))
    (tmp_path / "whitelist.config").write_text("git pull\n")
    rate_limiter._requests = []


class TestMcpAuthGuard:
    def test_mcp_without_key_is_401(self):
        # Guard rejects before reaching the MCP app - no lifespan needed.
        client = TestClient(app)
        resp = client.post("/mcp/", json=INITIALIZE, headers=HEADERS)
        assert resp.status_code == 401

    def test_mcp_wrong_key_is_401(self):
        client = TestClient(app)
        resp = client.post(
            "/mcp/", json=INITIALIZE, headers={**HEADERS, "X-API-Key": "wrong"}
        )
        assert resp.status_code == 401

    def test_mcp_401_body_is_valid_json(self):
        import json

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/mcp/", json=INITIALIZE, headers=HEADERS)  # no key
        assert resp.status_code == 401
        assert json.loads(resp.text) == {"error": "Missing or invalid credentials"}

    def test_mcp_initialize_with_key(self):
        # `with` runs the app lifespan so the MCP session manager is active.
        with TestClient(app) as client:
            resp = client.post(
                "/mcp/", json=INITIALIZE, headers={**HEADERS, "X-API-Key": "test-key"}
            )
            assert resp.status_code == 200
            # Streamable-HTTP replies as an SSE stream containing the server info.
            assert "ForPost" in resp.text

    def test_mcp_is_rate_limited(self, monkeypatch):
        # The ASGI guard applies the shared rate limiter to /mcp before the
        # request reaches the MCP app - so this needs neither the session
        # manager (no `with`) nor server-exception propagation from the first
        # few requests that do reach the (unstarted) app.
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
        rate_limiter._requests = []
        client = TestClient(app, raise_server_exceptions=False)
        codes = [
            client.post(
                "/mcp/", json=INITIALIZE, headers={**HEADERS, "X-API-Key": "test-key"}
            ).status_code
            for _ in range(6)
        ]
        assert 429 in codes  # throttled after the limit
