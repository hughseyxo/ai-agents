#!/usr/bin/env python3
"""
HTTP MCP Bridge Server — listens on Tailscale interface only (yopflix.tailed77a8.ts.net:4242).
Exposes 7 tools: run_agent, list_agents, get_agent_status, exec_shell,
read_file, write_file, list_directory.
Auth: Bearer token from MCP_BRIDGE_TOKEN env var.
Start: python3 mcp-servers/bridge_server.py
"""

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TAILSCALE_HOSTNAME = "yopflix.tailed77a8.ts.net"
BIND_HOST = socket.gethostbyname(TAILSCALE_HOSTNAME)
BIND_PORT = 4242
BRIDGE_TOKEN = os.environ.get("MCP_BRIDGE_TOKEN", "")
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "agents.db"
AGENT_REGISTRY = ["daily-briefing", "news-briefing", "security-audit"]
ALLOWED_PATH_PREFIX = "/home/cian/"
DEFAULT_WORKING_DIR = "/home/cian"
BLOCKED_PATTERNS = [
    "rm -rf /", "rm -rf ~", "dd if=", "mkfs", ":(){", ":(){ ",
    "> /dev/sda", "chmod -R 777 /", "wget -O- | sh",
    "curl | sh", "curl | bash",
]


# --- Tool implementations ---

def tool_list_agents(_args: dict) -> str:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        result = []
        for agent in AGENT_REGISTRY:
            row = conn.execute(
                "SELECT agent, status, started_at, finished_at, output_summary, error "
                "FROM runs WHERE agent = ? ORDER BY started_at DESC LIMIT 1",
                (agent,),
            ).fetchone()
            if row:
                result.append(dict(row))
            else:
                result.append({"agent": agent, "status": "never_run"})
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        try:
            conn.close()
        except Exception:
            pass


def tool_get_agent_status(args: dict) -> str:
    agent_name = args.get("agent_name", "").strip()
    if not agent_name:
        return json.dumps({"error": "agent_name is required"})
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        runs = conn.execute(
            "SELECT * FROM runs WHERE agent = ? ORDER BY started_at DESC LIMIT 5",
            (agent_name,),
        ).fetchall()
        if not runs:
            return json.dumps({"error": f"No runs found for agent: {agent_name}"})
        result = []
        for run in runs:
            d = dict(run)
            steps = conn.execute(
                "SELECT step, status, error, ts FROM steps WHERE run_id = ? ORDER BY ts",
                (d["id"],),
            ).fetchall()
            d["steps"] = [dict(s) for s in steps]
            result.append(d)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        try:
            conn.close()
        except Exception:
            pass


def tool_run_agent(args: dict) -> str:
    agent_name = args.get("agent_name", "").strip()
    task = args.get("task", "").strip()
    if not agent_name:
        return json.dumps({"error": "agent_name is required"})
    if agent_name not in AGENT_REGISTRY:
        return json.dumps({"error": f"Unknown agent: {agent_name}", "available": AGENT_REGISTRY})
    try:
        run_script = REPO_ROOT / "run-agent.sh"
        proc = subprocess.Popen(
            [str(run_script), agent_name],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return json.dumps({
            "status": "started",
            "agent": agent_name,
            "pid": proc.pid,
            "task": task or None,
            "note": "Running in background. Use get_agent_status to check progress.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_exec_shell(args: dict) -> str:
    command = args.get("command", "").strip()
    working_dir = args.get("working_dir", DEFAULT_WORKING_DIR).strip()
    if not command:
        return json.dumps({"error": "command is required"})
    cmd_lower = re.sub(r'\s+', ' ', command.lower()).strip()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return json.dumps({"error": f"Blocked: matches safety pattern '{pattern}'"})
    resolved = str(Path(working_dir).resolve())
    if not (resolved + "/").startswith(ALLOWED_PATH_PREFIX):
        return json.dumps({"error": f"working_dir must be under {ALLOWED_PATH_PREFIX}"})
    try:
        result = subprocess.run(
            command, shell=True, cwd=resolved,
            capture_output=True, text=True, timeout=60,
        )
        return json.dumps({
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Timed out after 60s"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_read_file(args: dict) -> str:
    path = args.get("path", "").strip()
    if not path:
        return json.dumps({"error": "path is required"})
    resolved = str(Path(path).expanduser().resolve())
    if not (resolved + "/").startswith(ALLOWED_PATH_PREFIX):
        return json.dumps({"error": f"path must be under {ALLOWED_PATH_PREFIX}"})
    try:
        content = Path(resolved).read_text(errors="replace")
        if len(content) > 100_000:
            content = content[:100_000] + "\n... [truncated at 100KB]"
        return json.dumps({"path": resolved, "content": content})
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_write_file(args: dict) -> str:
    path = args.get("path", "").strip()
    content = args.get("content", "")
    if not path:
        return json.dumps({"error": "path is required"})
    resolved = str(Path(path).expanduser().resolve())
    if not (resolved + "/").startswith(ALLOWED_PATH_PREFIX):
        return json.dumps({"error": f"path must be under {ALLOWED_PATH_PREFIX}"})
    if len(content) > 10_000_000:
        return json.dumps({"error": "content too large (max 10MB)"})
    try:
        p = Path(resolved)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return json.dumps({"status": "ok", "path": resolved, "bytes_written": len(content)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_list_directory(args: dict) -> str:
    path = args.get("path", DEFAULT_WORKING_DIR).strip()
    resolved = str(Path(path).expanduser().resolve())
    if not (resolved + "/").startswith(ALLOWED_PATH_PREFIX):
        return json.dumps({"error": f"path must be under {ALLOWED_PATH_PREFIX}"})
    try:
        entries = []
        for entry in sorted(Path(resolved).iterdir()):
            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": stat.st_size if entry.is_file() else None,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        truncated = False
        if len(entries) > 500:
            entries = entries[:500]
            truncated = True
        result = {"path": resolved, "entries": entries}
        if truncated:
            result["note"] = "Truncated at 500 entries"
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


TOOLS = [
    {
        "name": "list_agents",
        "description": "List available agents and their last run status from agents.db.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_agent_status",
        "description": "Get detailed run history and step results for a specific agent.",
        "inputSchema": {
            "type": "object", "required": ["agent_name"],
            "properties": {"agent_name": {"type": "string", "description": "daily-briefing, news-briefing, or security-audit"}},
        },
    },
    {
        "name": "run_agent",
        "description": "Start an AI agent in the background. Returns immediately with PID.",
        "inputSchema": {
            "type": "object", "required": ["agent_name"],
            "properties": {
                "agent_name": {"type": "string", "description": "daily-briefing, news-briefing, or security-audit"},
                "task": {"type": "string", "description": "Optional context note (logged only)"},
            },
        },
    },
    {
        "name": "exec_shell",
        "description": "Execute a shell command on the server (restricted to /home/cian/). Dangerous patterns are blocked.",
        "inputSchema": {
            "type": "object", "required": ["command"],
            "properties": {
                "command": {"type": "string"},
                "working_dir": {"type": "string", "default": "/home/cian"},
            },
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the server (restricted to /home/cian/).",
        "inputSchema": {
            "type": "object", "required": ["path"],
            "properties": {"path": {"type": "string", "description": "Absolute or ~ path"}},
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file on the server (restricted to /home/cian/).",
        "inputSchema": {
            "type": "object", "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
    },
    {
        "name": "list_directory",
        "description": "List directory contents on the server (restricted to /home/cian/).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": "/home/cian"}},
        },
    },
]

TOOL_HANDLERS = {
    "list_agents": tool_list_agents,
    "get_agent_status": tool_get_agent_status,
    "run_agent": tool_run_agent,
    "exec_shell": tool_exec_shell,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "list_directory": tool_list_directory,
}


def _make_ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _make_err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_jsonrpc(msg: dict) -> dict | None:
    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    if method == "initialize":
        return _make_ok(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-bridge", "version": "1.0.0"},
        })
    if method == "tools/list":
        return _make_ok(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _make_err(msg_id, -32601, f"Unknown tool: {name}")
        try:
            text = handler(params.get("arguments", {}))
            return _make_ok(msg_id, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            return _make_err(msg_id, -32000, str(e))
    if method == "notifications/initialized":
        return None
    if msg_id is not None:
        return _make_err(msg_id, -32601, f"Method not found: {method}")
    return None


class MCPBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] {self.address_string()} {fmt % args}", file=sys.stderr)

    def _auth_ok(self) -> bool:
        if not BRIDGE_TOKEN:
            return False
        return self.headers.get("Authorization", "") == f"Bearer {BRIDGE_TOKEN}"

    def _json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # /health is intentionally unauthenticated (liveness check)
        if self.path == "/health":
            self._json(200, {"status": "ok", "server": "mcp-bridge"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth_ok():
            self._json(401, {"error": "Unauthorized"})
            return
        if self.path not in ("/mcp", "/"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            msg = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as e:
            self._json(400, {"error": f"Invalid JSON: {e}"})
            return
        response = handle_jsonrpc(msg)
        if response is None:
            self.send_response(204)
            self.end_headers()
        else:
            self._json(200, response)


def main():
    if not BRIDGE_TOKEN:
        print("ERROR: MCP_BRIDGE_TOKEN not set. Create mcp-servers/.env.bridge.", file=sys.stderr)
        sys.exit(1)
    server = HTTPServer((BIND_HOST, BIND_PORT), MCPBridgeHandler)
    print(f"[bridge] Listening on http://{BIND_HOST}:{BIND_PORT}/mcp (Tailscale only)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
