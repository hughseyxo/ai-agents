# LLM CLI Failover: Claude → Gemini

**Date:** 2026-05-14  
**Status:** Planned  
**Problem:** When Claude Code runs out of tokens (happened May 2026 — several days of missed briefings), all agents using `synthesize()` silently fail. Gemini CLI is installed on this server but unused.

## Current MCP Setup

### What agents use
All agent prompts reference **custom MCP servers** (not Claude-native integrations):

| Service | Transport | Source | Tool prefix in prompts |
|---------|-----------|--------|----------------------|
| Todoist | HTTP (remote) | `ai.todoist.net/mcp` — Todoist's official hosted MCP | `mcp__todoist__` |
| Gmail | stdio (local) | `mcp-servers/gmail_server.py` | `mcp__gmail__` |
| Google Calendar | stdio (local) | `mcp-servers/calendar_server.py` | `mcp__google_calendar__` |

Config: `.mcp.json` (Claude reads this automatically).

### Claude-native integrations (NOT used by agents)
Claude Code also has built-in `mcp__claude_ai_Gmail__*`, `mcp__claude_ai_Google_Calendar__*`, and `mcp__claude_ai_Google_Drive__*` tools. These are **platform features** — they won't exist in Gemini. Our agent prompts don't reference them, so no impact.

### Claude-specific tools in prompts
Both briefing prompts use Claude-only features:
- **`ToolSearch`** — deferred tool loading. Gemini loads all tools immediately; these instructions must be stripped.
- **`WebFetch`** — built-in URL fetcher. News briefing uses this for RSS feeds. Gemini equivalent: shell tool with `curl`.
- **`mcp__*` namespace prefix** — Claude's naming convention. Gemini may use different prefixes (TBD — needs empirical check).

## Design

### Failover in `BaseAgent.synthesize()`
Single change point. Try Claude → on infrastructure failure → try Gemini.

```
synthesize(prompt)
  ├── Try claude CLI
  │   ├── Success → return output
  │   ├── Timeout → raise (terminal, no failover — see below)
  │   └── Failure (rate limit / empty output / quota)
  │       └── Adapt prompt for Gemini
  │           └── Try gemini CLI
  │               ├── Success → return output
  │               ├── Timeout → raise
  │               └── Failure → raise RuntimeError
  └── Non-retriable error (context_length, invalid_request)
      └── raise immediately (would fail on Gemini too)
```

**Failure detection:**
- Non-zero exit + stderr matching: rate limit, 429, quota, billing, connection, 502, 503
- Zero exit but empty stdout (partial failure mode)
- `subprocess.TimeoutExpired` at 600s → **terminal, no failover**

**Why timeouts don't fail over:** A timed-out CLI may have already executed
non-idempotent MCP side effects (sent email, created Todoist tasks, written
calendar events) before being killed. Running a second provider with the same
prompt would duplicate those effects. Confirmed in production 2026-05-15:
Claude timed out at 300s after sending the news briefing email; Gemini ran on
failover and sent a second copy. Fix: timeout raises terminally, and the 300s
window was raised to 600s so legitimate long runs aren't truncated.

**Prompt adaptation for Gemini** (`_adapt_prompt_for_gemini()`):
- Strip ToolSearch instructions
- Remap `mcp__servername__tool` → Gemini's convention (TBD)
- Replace `WebFetch` references → `curl` via shell

### Gemini MCP configuration
Mirror the 3 servers from `.mcp.json` into Gemini's config. One-time setup:
```bash
source .env && export TODOIST_API_TOKEN GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET
gemini mcp add todoist --transport http --url "https://ai.todoist.net/mcp" ...
gemini mcp add gmail --transport stdio -- python3 mcp-servers/gmail_server.py
gemini mcp add google-calendar --transport stdio -- python3 mcp-servers/calendar_server.py
```
Exact flags TBD — `gemini mcp add --help` needed to confirm syntax.

## Implementation steps

1. **Check `gemini mcp add` syntax** — run help, confirm exact flags for HTTP and stdio transports
2. **Add MCP servers to Gemini** — run the add commands
3. **Verify Gemini tool names** — `gemini -y -p "List all MCP tools" -o text` → determines namespace mapping
4. **Modify `agents/base.py`** — rewrite `synthesize()` with failover + `_adapt_prompt_for_gemini()`
5. **Test Gemini-only** — disable Claude, run `./run-agent.sh daily-briefing`
6. **Test failover** — re-enable Claude, verify it's preferred; simulate failure, verify Gemini takes over
7. **Update CLAUDE.md** — document failover in Agent Conventions

## Files to modify
- `agents/base.py` — synthesize() rewrite
- `CLAUDE.md` — document failover
- Gemini MCP config (via CLI commands, not a file we manage)

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Gemini MCP tool names differ from Claude's | High | Empirical check in step 3 before writing code |
| WebFetch → curl for RSS in news briefing | Medium | If flaky, move RSS fetching to Python (deterministic) |
| Gemini output quality/format differs | Low | Acceptable for personal email briefings |
| MCP server env vars not inherited | Low | subprocess.run inherits parent env by default; test in step 5 |
| Gemini HTTP MCP auth header syntax | Medium | Needs `gemini mcp add --help` to confirm Bearer token passing |

## Not in scope
- Automatic provider selection based on cost/speed (just failover)
- Gemini as primary provider
- Changes to agent prompts (adaptation is runtime, in Python)
- Security audit agent (doesn't use synthesize())
