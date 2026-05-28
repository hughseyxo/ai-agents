# LLM CLI Failover: Claude → Antigravity

**Date:** 2026-05-14 (Updated 2026-05-28 for Antigravity)
**Status:** Implemented
**Problem:** When Claude Code runs out of tokens, all agents using `synthesize()` silently fail. Antigravity CLI (`agy`) is used as the primary provider with Claude as fallback.

## Current MCP Setup

### What agents use
All agent prompts reference **custom MCP servers** (not Claude-native integrations):

| Service | Transport | Source | Tool prefix in prompts |
|---------|-----------|--------|----------------------|
| Todoist | HTTP (remote) | `ai.todoist.net/mcp` | `mcp__todoist__` |
| Gmail | stdio (local) | `mcp-servers/gmail_server.py` | `mcp__gmail__` |
| Google Calendar | stdio (local) | `mcp-servers/calendar_server.py` | `mcp__google_calendar__` |

Config: `.mcp.json` (Claude) and `~/.antigravity/antigravity-cli/mcp_config.json` (Antigravity).

### Claude-native integrations (NOT used by agents)
Claude Code also has built-in `mcp__claude_ai_Gmail__*` etc. These are platform features — they won't exist in Antigravity. Our agent prompts don't reference them.

### Claude-specific tools in prompts
Briefing prompts use Claude-only features adapted at runtime for Antigravity:
- **`ToolSearch`** — adapted to "Tools are already loaded".
- **`WebFetch`** — replaced with "the shell tool with curl".
- **`mcp__*` namespace prefix** — remapped to `mcp_name_tool`.

## Design

### Failover in `BaseAgent.synthesize()`
Single change point. Try Antigravity → on infrastructure failure → try Claude.

```
synthesize(prompt)
  ├── Try agy CLI (Antigravity)
  │   ├── Success → return output
  │   ├── Timeout → raise (terminal, no failover)
  │   └── Failure (rate limit / empty output / quota)
  │       └── Try claude CLI
  │           ├── Success → return output
  │           ├── Timeout → raise
  │           └── Failure → raise RuntimeError
  └── Non-retriable error (context_length, invalid_request)
      └── raise immediately (would fail on both)
```

**Prompt adaptation for Antigravity** (`_adapt_prompt_for_antigravity()`):
- Strip ToolSearch instructions
- Remap `mcp__servername__tool` → `mcp_name_tool`
- Replace `WebFetch` references → `curl` via shell

### Antigravity MCP configuration
Configured via `~/.antigravity/antigravity-cli/mcp_config.json`.
Note: HTTP servers use `serverUrl` instead of `url`.

## Implementation steps (Completed)

1. **Install Antigravity CLI** — `curl ... | bash`
2. **Configure MCP servers** — created `mcp_config.json` with transformed schema
3. **Verify tool names** — determined namespace mapping `mcp_name_tool`
4. **Modify `agents/base.py`** — renamed provider to `antigravity`, command to `agy`, updated adaptation logic
5. **Test migration** — verified `agy` succeeds and falls back to `claude` if needed
6. **Rename context files** — `GEMINI.md` → `.antigravity.md`
7. **Update documentation** — Updated `CLAUDE.md` and this file
