"""Tests for file operations endpoints."""

import base64
import json
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rate_limit import rate_limiter


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    """Configure env for all tests."""
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("WHITELIST_PATH", str(tmp_path / "whitelist.config"))
    (tmp_path / "whitelist.config").write_text("git pull\n")
    (tmp_path / "workspace").mkdir()
    (tmp_path / "logs").mkdir()
    rate_limiter._requests.clear()
    return tmp_path


@pytest.fixture
def client():
    return TestClient(app)


HEADERS = {"X-API-Key": "test-key"}


class TestWriteFile:
    def test_write_text_file(self, client, setup_env):
        resp = client.post(
            "/write_file",
            json={"path": "hello.txt", "content": "Hello World", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["size"] == len("Hello World".encode("utf-8"))
        # Verify file actually written
        written = (setup_env / "workspace" / "hello.txt").read_text()
        assert written == "Hello World"

    def test_write_base64_file(self, client, setup_env):
        binary_content = b"\x00\x01\x02\x03\xff"
        encoded = base64.b64encode(binary_content).decode()
        resp = client.post(
            "/write_file",
            json={"path": "data.bin", "content": encoded, "encoding": "base64", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["size"] == 5
        written = (setup_env / "workspace" / "data.bin").read_bytes()
        assert written == binary_content

    def test_write_creates_subdirectories(self, client, setup_env):
        resp = client.post(
            "/write_file",
            json={"path": "sub/dir/file.txt", "content": "nested", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert (setup_env / "workspace" / "sub" / "dir" / "file.txt").exists()

    def test_write_rejected_in_read_only(self, client, setup_env):
        resp = client.post(
            "/write_file",
            json={"path": "nope.txt", "content": "x", "mode": "read-only"},
            headers=HEADERS,
        )
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"]

    def test_write_allowed_in_whitelist_mode(self, client, setup_env):
        resp = client.post(
            "/write_file",
            json={"path": "ok.txt", "content": "whitelist write", "mode": "whitelist"},
            headers=HEADERS,
        )
        assert resp.status_code == 200

    def test_write_path_traversal_blocked(self, client, setup_env):
        resp = client.post(
            "/write_file",
            json={"path": "../../etc/passwd", "content": "hack", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 403
        assert "escapes" in resp.json()["detail"]

    def test_sibling_prefix_escape_blocked(self, client, setup_env):
        # A sibling dir sharing the workspace name prefix must NOT be writable
        # (regression for the old str.startswith check).
        resp = client.post(
            "/write_file",
            json={"path": "../workspace-evil/hack", "content": "x", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 403
        assert "escapes" in resp.json()["detail"]


class TestReadFile:
    def test_read_text_file(self, client, setup_env):
        (setup_env / "workspace" / "test.txt").write_text("contents here")
        resp = client.post(
            "/read_file",
            json={"path": "test.txt", "mode": "read-only"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "contents here"
        assert data["size"] == len("contents here".encode("utf-8"))

    def test_read_base64_file(self, client, setup_env):
        binary = b"\xde\xad\xbe\xef"
        (setup_env / "workspace" / "bin.dat").write_bytes(binary)
        resp = client.post(
            "/read_file",
            json={"path": "bin.dat", "encoding": "base64", "mode": "read-only"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        decoded = base64.b64decode(resp.json()["content"])
        assert decoded == binary

    def test_read_nonexistent_returns_404(self, client, setup_env):
        resp = client.post(
            "/read_file",
            json={"path": "nope.txt", "mode": "read-only"},
            headers=HEADERS,
        )
        assert resp.status_code == 404

    def test_read_allowed_in_all_modes(self, client, setup_env):
        (setup_env / "workspace" / "f.txt").write_text("data")
        for mode in ["read-only", "whitelist", "full-access"]:
            resp = client.post(
                "/read_file",
                json={"path": "f.txt", "mode": mode},
                headers=HEADERS,
            )
            assert resp.status_code == 200, f"Failed for mode={mode}"

    def test_read_paginates_and_caps(self, client, setup_env, monkeypatch):
        monkeypatch.setenv("MAX_OUTPUT_CHARS", "10")
        (setup_env / "workspace" / "big.txt").write_text("ABCDEFGHIJKLMNOP")  # 16
        r1 = client.post(
            "/read_file", json={"path": "big.txt", "mode": "read-only"}, headers=HEADERS
        ).json()
        assert r1["content"] == "ABCDEFGHIJ"  # first 10 bytes (the cap)
        assert r1["size"] == 16
        assert r1["truncated"] is True
        assert r1["next_offset"] == 10
        r2 = client.post(
            "/read_file",
            json={"path": "big.txt", "mode": "read-only", "offset": 10},
            headers=HEADERS,
        ).json()
        assert r2["content"] == "KLMNOP"
        assert r2["truncated"] is False

    def test_read_path_traversal_blocked(self, client, setup_env):
        resp = client.post(
            "/read_file",
            json={"path": "../../../etc/passwd", "mode": "read-only"},
            headers=HEADERS,
        )
        assert resp.status_code == 403

    @pytest.mark.skipif(
        os.name == "nt", reason="symlink creation needs privileges on Windows"
    )
    def test_read_symlink_escape_blocked(self, client, setup_env):
        # A symlink inside the workspace pointing outside must not be followable.
        outside = setup_env / "outside_secret.txt"
        outside.write_text("SECRET")
        link = setup_env / "workspace" / "link"
        os.symlink(str(outside), link)
        resp = client.post(
            "/read_file",
            json={"path": "link", "mode": "read-only"},
            headers=HEADERS,
        )
        assert resp.status_code == 403
        assert "escapes" in resp.json()["detail"]


class TestDeleteFile:
    def test_delete_existing_file(self, client, setup_env):
        (setup_env / "workspace" / "gone.txt").write_text("bye")
        resp = client.post(
            "/delete_file",
            json={"path": "gone.txt", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert not (setup_env / "workspace" / "gone.txt").exists()

    def test_delete_nonexistent_returns_404(self, client, setup_env):
        resp = client.post(
            "/delete_file",
            json={"path": "nope.txt", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 404

    def test_delete_rejected_in_read_only(self, client, setup_env):
        (setup_env / "workspace" / "keep.txt").write_text("keep")
        resp = client.post(
            "/delete_file",
            json={"path": "keep.txt", "mode": "read-only"},
            headers=HEADERS,
        )
        assert resp.status_code == 403
        assert (setup_env / "workspace" / "keep.txt").exists()

    def test_delete_path_traversal_blocked(self, client, setup_env):
        resp = client.post(
            "/delete_file",
            json={"path": "../../important", "mode": "full-access"},
            headers=HEADERS,
        )
        assert resp.status_code == 403


class TestFileOpsLogging:
    def test_write_logged_to_commands_jsonl(self, client, setup_env):
        client.post(
            "/write_file",
            json={"path": "log_test.txt", "content": "logged", "mode": "full-access"},
            headers=HEADERS,
        )
        jsonl = (setup_env / "logs" / "commands.jsonl").read_text().strip()
        entry = json.loads(jsonl)
        assert entry["operation"] == "write_file"
        assert entry["mode"] == "full-access"
        assert entry["size"] == 6

    def test_read_logged_to_commands_jsonl(self, client, setup_env):
        (setup_env / "workspace" / "r.txt").write_text("abc")
        client.post(
            "/read_file",
            json={"path": "r.txt", "mode": "read-only"},
            headers=HEADERS,
        )
        jsonl = (setup_env / "logs" / "commands.jsonl").read_text().strip()
        entry = json.loads(jsonl)
        assert entry["operation"] == "read_file"
        assert entry["mode"] == "read-only"

    def test_delete_logged_to_commands_jsonl(self, client, setup_env):
        (setup_env / "workspace" / "d.txt").write_text("x")
        client.post(
            "/delete_file",
            json={"path": "d.txt", "mode": "full-access"},
            headers=HEADERS,
        )
        jsonl = (setup_env / "logs" / "commands.jsonl").read_text().strip()
        entry = json.loads(jsonl)
        assert entry["operation"] == "delete_file"
