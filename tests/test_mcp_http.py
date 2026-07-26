"""Tests for the mounted MCP streamable-HTTP endpoint (/mcp) and its API-key guard.

These exercise the HTTP surface Claude custom connectors talk to, distinct from
test_mcp_server.py which calls the tools in-process.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

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

    def test_mcp_initialize_with_key(self):
        # `with` runs the app lifespan so the MCP session manager is active.
        with TestClient(app) as client:
            resp = client.post(
                "/mcp/", json=INITIALIZE, headers={**HEADERS, "X-API-Key": "test-key"}
            )
            assert resp.status_code == 200
            # Streamable-HTTP replies as an SSE stream containing the server info.
            assert "ForPost" in resp.text
