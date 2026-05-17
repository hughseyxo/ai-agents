# Laptop ↔ Server Claude Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable laptop Claude Code to reach server-side AI agents and resources via two mechanisms: a Tailscale-secured HTTP MCP server (Option 1) and registered RemoteTrigger tasks (Option 2).

**Architecture:** A new Python HTTP MCP server (`bridge_server.py`) listens only on the Tailscale interface (`yopflix.tailed77a8.ts.net:4242`), protected by a shared Bearer token. The laptop's Claude Code connects via HTTP MCP transport. Separately, four RemoteTrigger entries are registered to allow on-demand dispatch of server-side Claude Code sessions from the laptop.

**Tech Stack:** Python 3 stdlib (`http.server`, `sqlite3`, `subprocess`), systemd user services, Tailscale VPN, Claude Code RemoteTrigger API

---

## Option 1: MCP Bridge Server

### Files

- **Create:** `mcp-servers/bridge_server.py` — HTTP MCP server, 7 tools
- **Create:** `mcp-servers/.env.bridge` — `MCP_BRIDGE_TOKEN` secret (gitignored)
- **Create:** `mcp-bridge.service` — systemd user service unit
- **Symlink:** `~/.config/systemd/user/mcp-bridge.service` → above
- **Modify:** `.gitignore` — add `mcp-servers/.env.bridge`
- **Create:** `docs/mcp-bridge.md` — design doc (required by CLAUDE.md convention)
- **Modify (laptop):** `~/.mcp.json` — add `server-bridge` entry

---

### Task 1: Create the MCP Bridge Server

**Files:**
- Create: `mcp-servers/bridge_server.py`

- [ ] **Step 1: Write `bridge_server.py`**

```python
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
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BIND_HOST = "yopflix.tailed77a8.ts.net"
BIND_PORT = 4242
BRIDGE_TOKEN = os.environ.get("MCP_BRIDGE_TOKEN", "")
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "agents.db"
AGENT_REGISTRY = ["daily-briefing", "news-briefing", "security-audit"]
ALLOWED_PATH_PREFIX = "/home/cian"
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
        conn.close()
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


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
            conn.close()
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
        conn.close()
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_run_agent(args: dict) -> str:
    agent_name = args.get("agent_name", "").strip()
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
            "note": "Running in background. Use get_agent_status to check progress.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_exec_shell(args: dict) -> str:
    command = args.get("command", "").strip()
    working_dir = args.get("working_dir", DEFAULT_WORKING_DIR).strip()
    if not command:
        return json.dumps({"error": "command is required"})
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return json.dumps({"error": f"Blocked: matches safety pattern '{pattern}'"})
    resolved = str(Path(working_dir).resolve())
    if not resolved.startswith(ALLOWED_PATH_PREFIX):
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
    if not resolved.startswith(ALLOWED_PATH_PREFIX):
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
    if not resolved.startswith(ALLOWED_PATH_PREFIX):
        return json.dumps({"error": f"path must be under {ALLOWED_PATH_PREFIX}"})
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
    if not resolved.startswith(ALLOWED_PATH_PREFIX):
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
        return json.dumps({"path": resolved, "entries": entries})
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
```

- [ ] **Step 2: Verify syntax**

```bash
cd /home/cian/git/ai-agents
python3 -c "import ast; ast.parse(open('mcp-servers/bridge_server.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /home/cian/git/ai-agents
git add mcp-servers/bridge_server.py
git commit -m "feat: add HTTP MCP bridge server for laptop access over Tailscale"
```

---

### Task 2: Secrets and gitignore

**Files:**
- Create: `mcp-servers/.env.bridge`
- Modify: `.gitignore`

- [ ] **Step 1: Generate token and create env file**

```bash
# Generate a strong token and write the env file
python3 -c "import secrets; print('MCP_BRIDGE_TOKEN=' + secrets.token_hex(32))" > /home/cian/git/ai-agents/mcp-servers/.env.bridge
chmod 600 /home/cian/git/ai-agents/mcp-servers/.env.bridge
cat /home/cian/git/ai-agents/mcp-servers/.env.bridge  # verify it looks like: MCP_BRIDGE_TOKEN=<64 hex chars>
```

- [ ] **Step 2: Add to .gitignore**

```bash
grep -q "mcp-servers/.env.bridge" /home/cian/git/ai-agents/.gitignore || \
  echo "mcp-servers/.env.bridge" >> /home/cian/git/ai-agents/.gitignore
git add .gitignore
git commit -m "chore: gitignore MCP bridge token env file"
```

---

### Task 3: Create the systemd service

**Files:**
- Create: `mcp-bridge.service`
- Symlink: `~/.config/systemd/user/mcp-bridge.service`

- [ ] **Step 1: Write service file**

```ini
# /home/cian/git/ai-agents/mcp-bridge.service
[Unit]
Description=MCP Bridge Server (Tailscale HTTP)
After=network.target tailscaled.service

[Service]
Type=simple
WorkingDirectory=/home/cian/git/ai-agents
EnvironmentFile=/home/cian/git/ai-agents/mcp-servers/.env.bridge
ExecStart=/usr/bin/python3 -u mcp-servers/bridge_server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Create at `/home/cian/git/ai-agents/mcp-bridge.service`.

- [ ] **Step 2: Symlink and enable**

```bash
ln -sf /home/cian/git/ai-agents/mcp-bridge.service ~/.config/systemd/user/mcp-bridge.service
systemctl --user daemon-reload
systemctl --user enable mcp-bridge.service
systemctl --user start mcp-bridge.service
systemctl --user status mcp-bridge.service --no-pager
```
Expected: `Active: active (running)`

- [ ] **Step 3: Commit service file**

```bash
cd /home/cian/git/ai-agents
git add mcp-bridge.service
git commit -m "feat: systemd user service for MCP bridge server"
```

---

### Task 4: Smoke test the server

- [ ] **Step 1: Test health endpoint (from server)**

```bash
curl -s http://yopflix.tailed77a8.ts.net:4242/health
```
Expected: `{"status": "ok", "server": "mcp-bridge"}`

- [ ] **Step 2: Test auth rejection**

```bash
curl -s -X POST http://yopflix.tailed77a8.ts.net:4242/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}'
```
Expected: `{"error": "Unauthorized"}` with HTTP 401.

- [ ] **Step 3: Test initialize with token**

```bash
source /home/cian/git/ai-agents/mcp-servers/.env.bridge
curl -s -X POST http://yopflix.tailed77a8.ts.net:4242/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${MCP_BRIDGE_TOKEN}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}' | python3 -m json.tool
```
Expected: response with `"serverInfo": {"name": "mcp-bridge", "version": "1.0.0"}`.

- [ ] **Step 4: Test list_agents tool**

```bash
source /home/cian/git/ai-agents/mcp-servers/.env.bridge
curl -s -X POST http://yopflix.tailed77a8.ts.net:4242/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${MCP_BRIDGE_TOKEN}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_agents","arguments":{}}}' | python3 -m json.tool
```
Expected: JSON with array of 3 agents (daily-briefing, news-briefing, security-audit) and their last run status.

---

### Task 5: Configure the laptop (Windows)

**Action: performed on the Windows laptop (not the server)**

- [ ] **Step 1: Get the token value from the server**

```bash
# On server:
cat /home/cian/git/ai-agents/mcp-servers/.env.bridge
# Copy the token value (the 64-char hex string after MCP_BRIDGE_TOKEN=)
```

- [ ] **Step 2: Set token as a Windows User Environment Variable**

Open PowerShell and run (replace `<token>` with the copied value):
```powershell
[System.Environment]::SetEnvironmentVariable("MCP_BRIDGE_TOKEN", "<token>", "User")
```
This persists across reboots. Verify with: `$env:MCP_BRIDGE_TOKEN` (after opening a new terminal).

Alternatively via GUI: Start → "Edit the system environment variables" → "Environment Variables" → New User Variable → Name: `MCP_BRIDGE_TOKEN`, Value: `<token>`.

- [ ] **Step 3: Add server-bridge to laptop's `.mcp.json`**

The file lives at `%USERPROFILE%\.mcp.json` (e.g. `C:\Users\Cian\.mcp.json`). Open PowerShell:

```powershell
# Check if it exists first
Test-Path "$env:USERPROFILE\.mcp.json"
```

If it **does not exist**, create it:
```powershell
@'
{
  "mcpServers": {
    "server-bridge": {
      "type": "http",
      "url": "http://yopflix.tailed77a8.ts.net:4242/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_BRIDGE_TOKEN}"
      }
    }
  }
}
'@ | Set-Content "$env:USERPROFILE\.mcp.json" -Encoding UTF8
```

If it **already exists**, open it in Notepad and add the `server-bridge` block inside the existing `mcpServers` object:
```json
"server-bridge": {
  "type": "http",
  "url": "http://yopflix.tailed77a8.ts.net:4242/mcp",
  "headers": {
    "Authorization": "Bearer ${MCP_BRIDGE_TOKEN}"
  }
}
```

- [ ] **Step 4: Restart Claude Code on the laptop**

Close and reopen Claude Code so it picks up the new environment variable and `.mcp.json`.

- [ ] **Step 5: Test from laptop Claude Code**

In a new Claude Code session on the laptop:
```
Use ToolSearch to find "list_agents"
```
It should find `list_agents` from `server-bridge`. Then:
```
Call list_agents()
```
Expected: same 3-agent list as the server curl test. Then:
```
Call exec_shell with command "hostname"
```
Expected: `ns3069668`

---

### Task 6: Write design doc (required by CLAUDE.md)

**Files:**
- Create: `docs/mcp-bridge.md`

- [ ] **Step 1: Write design doc**

```markdown
# MCP Bridge Server

**Problem:** Laptop Claude Code has no access to server-side tools (AI agents, files, shell).

**Design:** HTTP MCP server bound to Tailscale IP only (yopflix.tailed77a8.ts.net:4242). Bearer token auth (shared secret, never transmitted over public internet). 7 tools: list_agents, get_agent_status, run_agent, exec_shell, read_file, write_file, list_directory.

**Security:** Tailscale-only binding (no 0.0.0.0), Bearer token, path restriction to /home/cian/, command blocklist for shell exec, 60s timeout.

**Files:** mcp-servers/bridge_server.py, mcp-servers/.env.bridge (gitignored), mcp-bridge.service

**Laptop config:** ~/.mcp.json → mcpServers.server-bridge (HTTP transport, Tailscale IP)
```

- [ ] **Step 2: Commit**

```bash
cd /home/cian/git/ai-agents
git add docs/mcp-bridge.md
git commit -m "docs: MCP bridge server design doc"
```

---

## Option 2: RemoteTrigger Setup

**Note:** RemoteTrigger calls must be executed from a live Claude Code session (not scripted). Run these steps interactively using the `RemoteTrigger` tool.

### Task 7: Create RemoteTrigger entries on the server

These must be run from the **server's** Claude Code session. Load RemoteTrigger via `ToolSearch("select:RemoteTrigger")` first.

- [ ] **Step 1: Check existing triggers**

```
RemoteTrigger(action="list")
```
Note the existing stale `Daily Briefing` trigger ID (currently points to `workflows/daily-briefing.md` which no longer exists).

- [ ] **Step 2: Create `run-briefing-agent` trigger**

```
RemoteTrigger(action="create", body={
  "name": "run-briefing-agent",
  "enabled": true,
  "job_config": {
    "ccr": {
      "events": [{"data": {"message": {
        "role": "user",
        "content": "Run the daily briefing agent: cd /home/cian/git/ai-agents && bash run-agent.sh daily-briefing\n\nReport the output summary or any errors.",
        "uuid": "run-briefing-agent-v1", "type": "user"
      }}}],
      "session_context": {
        "allowed_tools": ["Bash", "Read"],
        "model": "claude-sonnet-4-6"
      }
    }
  }
})
```
Save the returned `trigger_id`.

- [ ] **Step 3: Create `check-agent-health` trigger**

```
RemoteTrigger(action="create", body={
  "name": "check-agent-health",
  "enabled": true,
  "job_config": {
    "ccr": {
      "events": [{"data": {"message": {
        "role": "user",
        "content": "Check all agents health:\nsqlite3 /home/cian/git/ai-agents/data/agents.db \"SELECT agent, status, started_at, error FROM runs GROUP BY agent HAVING started_at = MAX(started_at) ORDER BY agent;\"\n\nAlso check crontab: crontab -l | grep ai-agents\n\nReport: last run time+status per agent, any agent not run in 25+ hours, cron installed Y/N, PASS/FAIL summary.",
        "uuid": "check-agent-health-v1", "type": "user"
      }}}],
      "session_context": {
        "allowed_tools": ["Bash"],
        "model": "claude-haiku-4-5"
      }
    }
  }
})
```
Save the returned `trigger_id`.

- [ ] **Step 4: Create `security-audit` trigger**

```
RemoteTrigger(action="create", body={
  "name": "security-audit",
  "enabled": true,
  "job_config": {
    "ccr": {
      "events": [{"data": {"message": {
        "role": "user",
        "content": "Run the security audit agent: cd /home/cian/git/ai-agents && bash run-agent.sh security-audit\n\nReport full output and highlight Critical/High findings.",
        "uuid": "security-audit-v1", "type": "user"
      }}}],
      "session_context": {
        "allowed_tools": ["Bash", "Read"],
        "model": "claude-sonnet-4-6"
      }
    }
  }
})
```
Save the returned `trigger_id`.

- [ ] **Step 5: Update the stale Daily Briefing trigger**

Use the ID from Step 1. Update it to use the Python agent instead of the old workflow file:

```
RemoteTrigger(action="update", trigger_id="<stale-trigger-id>", body={
  "job_config": {
    "ccr": {
      "events": [{"data": {"message": {
        "role": "user",
        "content": "Run the daily briefing agent: cd /home/cian/git/ai-agents && bash run-agent.sh daily-briefing\n\nReport the output summary or any errors.",
        "uuid": "daily-briefing-v2", "type": "user"
      }}}]
    }
  }
})
```

- [ ] **Step 6: Verify all triggers**

```
RemoteTrigger(action="list")
```
Expected: 4 triggers (3 new + 1 updated), all `enabled: true`.

---

### Task 8: Test RemoteTrigger from the laptop

- [ ] **Step 1: Load RemoteTrigger on laptop**

In a laptop Claude Code session:
```
ToolSearch("select:RemoteTrigger")
```

- [ ] **Step 2: List triggers to confirm they're visible**

```
RemoteTrigger(action="list")
```
Expected: Same 4 triggers visible from laptop (shared Claude account).

- [ ] **Step 3: Test fire `check-agent-health`**

```
RemoteTrigger(action="run", trigger_id="<check-agent-health-trigger-id>")
```
Expected: Trigger fires, returns a run URL. Open the URL in browser to see results. The run should complete in ~30s and show agent health table.

---

## Verification Summary

| Check | Command | Expected |
|-------|---------|----------|
| Bridge running | `systemctl --user status mcp-bridge` | `active (running)` |
| Auth works | `curl -H "Authorization: Bearer $TOKEN" http://yopflix.tailed77a8.ts.net:4242/health` | `{"status":"ok"}` |
| Auth blocks | `curl http://yopflix.tailed77a8.ts.net:4242/mcp -X POST ...` (no header) | `401 Unauthorized` |
| Tailscale-only | `curl http://37.187.226.57:4242/health` (public IP) | Connection refused |
| MCP on laptop | Open laptop Claude Code, call `list_agents()` | 3 agents with DB status |
| Shell exec | Call `exec_shell("hostname")` from laptop | `ns3069668` |
| RemoteTrigger | Laptop: `RemoteTrigger(action="run", ...)` | Trigger fires, URL returned |
