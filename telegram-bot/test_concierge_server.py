"""Tests for the concierge MCP server (stdio JSON-RPC).

Drives the server as a subprocess: pipes initialize / tools/list / tools/call
JSON-RPC lines to stdin and asserts on the responses. Tool calls hit real
tools.py functions, so DB-backed calls are mocked via a patched read where
needed; here we use get_plant_status which gracefully returns a string even
with an empty/real DB.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SERVER = REPO_ROOT / "mcp-servers" / "concierge_server.py"


def _run_server(messages: list[dict]) -> list[dict]:
    """Feed JSON-RPC messages via stdin, return parsed response objects."""
    stdin = "\n".join(json.dumps(m) for m in messages) + "\n"
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input=stdin, capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT),
    )
    responses = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses


def test_initialize_returns_server_info():
    resps = _run_server([{"jsonrpc": "2.0", "id": 1, "method": "initialize"}])
    assert resps[0]["id"] == 1
    assert resps[0]["result"]["serverInfo"]["name"] == "concierge"


def test_tools_list_includes_concierge_tools():
    resps = _run_server([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    names = {t["name"] for t in resps[0]["result"]["tools"]}
    assert "get_plant_status" in names
    assert "water_plants" in names
    assert "get_agent_status" in names


def test_tools_call_returns_text_content():
    resps = _run_server([{
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "get_plant_status", "arguments": {}},
    }])
    content = resps[0]["result"]["content"]
    assert content[0]["type"] == "text"
    assert isinstance(content[0]["text"], str)


def test_tools_call_serializes_non_string_return():
    # get_all_plants returns a list — server must JSON-serialize it to text
    resps = _run_server([{
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "get_all_plants", "arguments": {}},
    }])
    text = resps[0]["result"]["content"][0]["text"]
    json.loads(text)  # must be valid JSON


def test_unknown_tool_returns_error():
    resps = _run_server([{
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "no_such_tool", "arguments": {}},
    }])
    assert "error" in resps[0]
