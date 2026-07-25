"""Integration tests for the ForPost API endpoint."""

import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    """Configure env for all tests."""
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("WHITELIST_PATH", str(tmp_path / "whitelist.config"))
    # Create a whitelist for whitelist mode tests
    wl = tmp_path / "whitelist.config"
    wl.write_text("git pull\ndocker restart\n")
    return tmp_path


@pytest.fixture
def client():
    return TestClient(app)


HEADERS = {"X-API-Key": "test-key"}


class TestIntegration:
    def test_valid_read_only_command(self, client, setup_env):
        response = client.post(
            "/execute",
            json={"command": "echo hello", "mode": "read-only"},
            headers=HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert "hello" in data["stdout"]
        assert data["exit_code"] == 0

        # Verify command was logged
        jsonl = (setup_env / "commands.jsonl").read_text().strip()
        entry = json.loads(jsonl)
        assert entry["command"] == "echo hello"
        assert entry["mode"] == "read-only"

    def test_write_command_in_read_only_rejected(self, client, setup_env):
        response = client.post(
            "/execute",
            json={"command": "rm -rf /tmp/test", "mode": "read-only"},
            headers=HEADERS,
        )
        assert response.status_code == 403
        assert "not allowed" in response.json()["detail"]

        # Should NOT be logged in commands.jsonl
        jsonl_file = setup_env / "commands.jsonl"
        assert not jsonl_file.exists() or jsonl_file.read_text().strip() == ""

    def test_missing_api_key_returns_401(self, client, setup_env):
        response = client.post(
            "/execute",
            json={"command": "echo hello", "mode": "read-only"},
        )
        assert response.status_code == 401

        # No command logged
        jsonl_file = setup_env / "commands.jsonl"
        assert not jsonl_file.exists() or jsonl_file.read_text().strip() == ""

    def test_invalid_api_key_returns_401(self, client):
        response = client.post(
            "/execute",
            json={"command": "echo hello", "mode": "read-only"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_full_access_allows_any_command(self, client, setup_env):
        response = client.post(
            "/execute",
            json={"command": "echo full access", "mode": "full-access"},
            headers=HEADERS,
        )
        assert response.status_code == 200
        assert "full access" in response.json()["stdout"]

    def test_whitelist_allows_matching_command(self, client, setup_env):
        response = client.post(
            "/execute",
            json={"command": "git pull --rebase", "mode": "whitelist"},
            headers=HEADERS,
        )
        # git pull will fail (not a git repo) but that's fine - it should
        # not be rejected by mode validation
        assert response.status_code == 200
        # The command ran (even if it failed), so it should be logged
        jsonl_file = setup_env / "commands.jsonl"
        assert jsonl_file.exists()

    def test_whitelist_rejects_non_matching_command(self, client, setup_env):
        response = client.post(
            "/execute",
            json={"command": "rm -rf /", "mode": "whitelist"},
            headers=HEADERS,
        )
        assert response.status_code == 403
        assert "not in whitelist" in response.json()["detail"]

    def test_invalid_mode_returns_422(self, client):
        response = client.post(
            "/execute",
            json={"command": "echo hello", "mode": "invalid-mode"},
            headers=HEADERS,
        )
        assert response.status_code == 422

    def test_timed_out_field_false_for_fast_command(self, client, setup_env):
        response = client.post(
            "/execute",
            json={"command": "echo quick", "mode": "full-access"},
            headers=HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["timed_out"] is False

    def test_timed_out_field_present_in_response(self, client, setup_env):
        response = client.post(
            "/execute",
            json={"command": "echo hello", "mode": "full-access"},
            headers=HEADERS,
        )
        assert "timed_out" in response.json()
