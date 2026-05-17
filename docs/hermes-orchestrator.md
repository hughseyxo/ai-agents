# Hermes Agent as Telegram Orchestrator

**Status:** Implementing
**Date:** 2026-05-16

## Problem

The user wants a single persistent chat interface (Telegram) backed by an AI agent that can route work to Claude Code and Gemini CLI without burning Pro-plan quotas on routine orchestration turns.

Hermes Agent v0.13.0 is installed and Telegram is configured, but the current model backend (`gemini-3-flash-preview` with Anthropic fallback) is broken in practice:
- Gemini cloud quota exhausts almost immediately (`HTTP 429` in `~/.hermes/logs/errors.log` after a single `test` message).
- Anthropic API rejects with `HTTP 400: Third-party apps now draw from your extra usage, not your plan limits` — Claude Pro does not grant API credits.

Net effect: every Hermes turn fails, and even if it didn't, every turn would burn Pro-plan capacity that should be reserved for actual heavy work.

## Design decisions

1. **Orchestrator brain runs locally via Ollama (Hermes-3 8B Q4_K_M).** Trade-off: 2–4 tok/s on this Xeon D-1521; routing turns 5–15s, end-to-end Telegram round-trips 30–90s. Acceptable because it preserves Claude/Gemini Pro CLI quotas for real work.
2. **Heavy generation routes to Claude/Gemini CLI subprocesses,** invoked via Hermes' built-in `terminal` toolset (`claude -p "..."`, `gemini -p "..."`). These use OAuth Pro quotas, separate from API.
3. **No custom Python tools.** SOUL.md instructs Hermes to use the existing `terminal` toolset for the three "tools" originally specified (run_claude, run_gemini, read_agent_log). Less code to maintain.
4. **No shared memory MCP.** Hermes' built-in memory (`~/.hermes/memories/`) is sufficient; Claude Code and Gemini CLI keep separate memory stores. Revisit if cross-tool memory becomes important.
5. **No cloud fallback.** If Ollama dies, Hermes is down; systemd restarts it. Avoids silent quota burn and confusing chat-side errors.
6. **Approvals stay manual** for `terminal` calls during initial bring-up. Allowlist `claude -p *`, `gemini -p *`, `tail` patterns later once trusted.
7. **Hermes runs as a user systemd service.** Matches existing `free_time_bot.service` pattern. Requires `loginctl enable-linger cian` (already required by other bots — see [[project_user_systemd_linger]]).

## Architecture

```
Telegram → hermes gateway (systemd --user) → Hermes Agent (Hermes-3 8B via Ollama localhost)
                                              ├─ MCP: Todoist (HTTP, reuse TODOIST_API_TOKEN)
                                              ├─ Built-in toolsets: terminal, memory, todo,
                                              │   file, delegation, web, code_execution
                                              └─ Routes heavy work:
                                                  claude -p "..."   (Pro OAuth)
                                                  gemini -p "..."   (Pro OAuth)
                                                  tail -n N <log>   (read agent logs)
```

## Files touched

| Path | Action |
|---|---|
| `~/.hermes/config.yaml` | Patch `model.*`, `fallback_model: []`, add `mcp_servers.todoist` |
| `~/.hermes/.env` | Add `OLLAMA_API_KEY=ollama`, `TODOIST_API_TOKEN=...` |
| `~/.hermes/SOUL.md` | Rewrite: terse router persona, routing rules, memory protocol |
| `~/.config/systemd/user/hermes-gateway.service` | New |
| `/etc/systemd/system/ollama.service` | Created by Ollama installer; tweak `OLLAMA_KEEP_ALIVE=-1` |
| `/home/cian/git/ai-agents/CLAUDE.md` | Note Hermes is the persistent chat frontend |
| `/home/cian/git/ai-agents/docs/hermes-orchestrator.md` | This doc |

## Out of scope

- Custom MCP server wrapping CLI subprocesses (deferred unless approval friction proves intolerable)
- Shared memory JSONL across Hermes / Claude Code / Gemini CLI
- A separate Python Telegram bot (Hermes gateway handles it natively)
- Smaller/faster local model swap (Hermes-3 3B, Qwen2.5-3B) — future option if 8B too slow
- Pre-allowlisting commands in `command_allowlist`

## Risk / reality check

- **CPU is borderline:** Xeon D-1521 (4c/8t, 2.4 GHz, AVX2 only). Inference will feel slow. If unworkable after a week's use, swap model down to 3B or move orchestrator brain to a paid endpoint.
- **Disk is 92% full** (900 GB free). Model fits; flag if it climbs.
- **Manual approval in Telegram UX:** user must tap approve for each `claude -p` invocation — this will get old fast. Plan to allowlist after a few sessions of observed-safe patterns.
