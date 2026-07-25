"""Phase 2 tests: the MCP wrapper (app/mcp_server.py).

Verifies the tool schemas are valid and that calling the MCP tools maps to the
same logic as the equivalent HTTP endpoints (main.py). Tests are synchronous
and drive the async FastMCP API via asyncio.run, so no async pytest plugin is
required.
"""

import asyncio
import base64
import json

import pytest

from app.mcp_server import mcp
from app.rate_limit import rate_limiter
from mcp.server.fastmcp.exceptions import ToolError


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    """Isolate env per test, mirroring test_main.py."""
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("WHITELIST_PATH", str(tmp_path / "whitelist.config"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    (tmp_path / "whitelist.config").write_text("git pull\ndocker restart\n")
    # The rate limiter is a process-wide singleton; reset so tests are independent.
    rate_limiter._requests = []
    return tmp_path


def run(coro):
    """Run an async FastMCP call to completion."""
    return asyncio.run(coro)


def call(name, args):
    """Call a tool and return its parsed JSON payload."""
    result = run(mcp.call_tool(name, args))
    # Across mcp versions call_tool returns either a content list or a
    # (content, structured) tuple - handle both.
    if isinstance(result, tuple):
        result = result[0]
    return json.loads(result[0].text)


def list_tools():
    return run(mcp.list_tools())


class TestSchema:
    def test_all_four_tools_exposed(self):
        names = {t.name for t in list_tools()}
        assert names == {
            "execute_command",
            "write_file_tool",
            "read_file_tool",
            "delete_file_tool",
        }

    def test_execute_command_schema(self):
        ec = next(t for t in list_tools() if t.name == "execute_command")
        props = ec.inputSchema["properties"]
        assert set(props) == {"command", "mode"}
        assert set(ec.inputSchema["required"]) == {"command", "mode"}

    def test_file_tool_schemas(self):
        tools = {t.name: t for t in list_tools()}
        write = tools["write_file_tool"]
        assert set(write.inputSchema["properties"]) == {
            "path",
            "content",
            "mode",
            "encoding",
        }
        # encoding has a default, so only path/content/mode are required
        assert set(write.inputSchema["required"]) == {"path", "content", "mode"}


class TestExecuteCommand:
    def test_read_only_echo_matches_http(self, setup_env):
        res = call("execute_command", {"command": "echo hello", "mode": "read-only"})
        assert "hello" in res["stdout"]
        assert res["exit_code"] == 0
        assert res["timed_out"] is False

        entry = json.loads((setup_env / "commands.jsonl").read_text().strip())
        assert entry["command"] == "echo hello"
        assert entry["mode"] == "read-only"
        assert entry["operation"] == "execute_command"

    def test_read_only_rejects_write(self, setup_env):
        with pytest.raises(ToolError, match="not allowed in read-only mode"):
            call("execute_command", {"command": "rm -rf /tmp/x", "mode": "read-only"})
        # Rejected commands are not executed, so nothing is logged.
        jsonl = setup_env / "commands.jsonl"
        assert not jsonl.exists() or jsonl.read_text().strip() == ""

    def test_whitelist_rejects_non_matching(self):
        with pytest.raises(ToolError, match="not in whitelist"):
            call("execute_command", {"command": "rm -rf /", "mode": "whitelist"})

    def test_full_access_allows_any(self):
        res = call("execute_command", {"command": "echo full", "mode": "full-access"})
        assert "full" in res["stdout"]

    def test_missing_required_param_errors_and_no_execution(self, setup_env):
        with pytest.raises(ToolError):
            call("execute_command", {"command": "echo hi"})
        jsonl = setup_env / "commands.jsonl"
        assert not jsonl.exists() or jsonl.read_text().strip() == ""

    def test_invalid_mode_errors(self):
        with pytest.raises(ToolError):
            call("execute_command", {"command": "echo hi", "mode": "bogus"})


class TestFileTools:
    def test_text_roundtrip_and_delete(self, setup_env):
        written = call(
            "write_file_tool",
            {"path": "notes.txt", "content": "hello file", "mode": "full-access"},
        )
        assert written["size"] == len(b"hello file")

        read = call("read_file_tool", {"path": "notes.txt", "mode": "read-only"})
        assert read["content"] == "hello file"

        deleted = call(
            "delete_file_tool", {"path": "notes.txt", "mode": "full-access"}
        )
        assert deleted["path"].endswith("notes.txt")

        # Three file operations logged.
        lines = (setup_env / "commands.jsonl").read_text().strip().splitlines()
        ops = [json.loads(line)["operation"] for line in lines]
        assert ops == ["write_file", "read_file", "delete_file"]

    def test_base64_roundtrip(self, setup_env):
        raw = bytes([0, 1, 2, 255, 254])
        encoded = base64.b64encode(raw).decode("ascii")
        call(
            "write_file_tool",
            {
                "path": "bin.dat",
                "content": encoded,
                "mode": "full-access",
                "encoding": "base64",
            },
        )
        read = call(
            "read_file_tool",
            {"path": "bin.dat", "mode": "read-only", "encoding": "base64"},
        )
        assert base64.b64decode(read["content"]) == raw

    def test_write_rejected_in_read_only(self, setup_env):
        with pytest.raises(ToolError, match="not allowed in read-only mode"):
            call(
                "write_file_tool",
                {"path": "x.txt", "content": "no", "mode": "read-only"},
            )

    def test_delete_rejected_in_read_only(self, setup_env):
        with pytest.raises(ToolError, match="not allowed in read-only mode"):
            call("delete_file_tool", {"path": "x.txt", "mode": "read-only"})

    def test_read_missing_file_errors(self, setup_env):
        with pytest.raises(ToolError, match="not found"):
            call("read_file_tool", {"path": "nope.txt", "mode": "read-only"})
