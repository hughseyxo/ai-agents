# MCP Bridge Server

**Problem:** Laptop Claude Code has no access to server-side tools — AI agents, files, shell commands — so work that requires the server must be done manually or via SSH.

**Design decisions:**
- HTTP MCP transport (not stdio) so the laptop can connect over the network
- Binds to Tailscale IP only (`100.96.86.73:4242`) — no public internet exposure
- Bearer token auth (shared secret) — never transmitted over public internet thanks to Tailscale
- Python stdlib only (no third-party deps) — keeps the server lightweight and dependency-free
- Systemd user service for auto-start and automatic restart on failure

**Architecture:**

```
Laptop Claude Code
  └─ ~/.mcp.json → mcpServers.server-bridge (HTTP, Tailscale IP)
       └─ POST http://100.96.86.73:4242/mcp (Bearer token)
            └─ mcp-servers/bridge_server.py
                 ├─ list_agents / get_agent_status / run_agent → data/agents.db + run-agent.sh
                 ├─ exec_shell → subprocess.run (restricted to /home/cian/, blocklist)
                 └─ read_file / write_file / list_directory → pathlib (restricted to /home/cian/)
```

**Security model:**
- Tailscale-only binding: port 4242 is not reachable from the public internet
- Bearer token: shared secret in `mcp-servers/.env.bridge` (gitignored, chmod 600)
- Path restriction: all file/shell tools restricted to `/home/cian/`
- Command blocklist: blocks `rm -rf /`, `dd if=`, `mkfs`, fork bombs, etc. (normalises whitespace before matching)
- 60s shell timeout, 100KB read limit, 10MB write limit, 500-entry directory cap

**Data model:**
- Agent status read from `data/agents.db` (SQLite) — `runs` and `steps` tables
- Agents run via `run-agent.sh <name>` (existing entrypoint, sources `.env`, refreshes Google token)

**Files:**
- `mcp-servers/bridge_server.py` — HTTP MCP server (all 7 tools)
- `mcp-servers/.env.bridge` — `MCP_BRIDGE_TOKEN` secret (gitignored)
- `mcp-bridge.service` — systemd user service unit (symlinked to `~/.config/systemd/user/`)

**Laptop setup (Windows):**
1. Set `MCP_BRIDGE_TOKEN` as a Windows User Environment Variable (PowerShell: `[System.Environment]::SetEnvironmentVariable("MCP_BRIDGE_TOKEN", "<token>", "User")`)
2. Add to `%USERPROFILE%\.mcp.json`:
   ```json
   "server-bridge": {
     "type": "http",
     "url": "http://100.96.86.73:4242/mcp",
     "headers": { "Authorization": "Bearer ${MCP_BRIDGE_TOKEN}" }
   }
   ```
3. Restart Claude Code to pick up the new env var and MCP config
