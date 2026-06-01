# Design: Claude CLI as the Concierge Bot's Conversational Backend

**Date:** 2026-05-31
**Status:** Implemented

## Problem

The Telegram concierge bot (`telegram-bot/bot.py`) ran on free OpenRouter models, whose
conversational quality is well below Claude. Goal: "Claude levels of natural discussion"
without incurring API billing.

## Design Decisions

- **Backend:** the installed `claude` CLI (v2.1.158) in `-p`/print mode, running on the
  user's Pro subscription (no API billing). Matches existing repo usage
  (`agents/base.py`, `free_time_bot.py`).
- **Model:** `claude-sonnet-4-6` (fast, excellent; constant `CLAUDE_MODEL` in
  `claude_backend.py`).
- **Tool scope:** concierge tools only — `--strict-mcp-config` loads *only* the new
  concierge MCP server, so Todoist/Calendar/Gmail are NOT exposed to the bot.
- **Tool use:** the bot's `tools.py` functions are surfaced to the CLI via a new stdio
  MCP server (`mcp-servers/concierge_server.py`), so Claude calls them natively
  (`mcp__concierge__*`) — including actions like `water_plant`/`water_plants`.
- **Multi-turn memory:** per-chat `session_id` captured from the CLI's JSON output and
  passed back via `--resume`. In-memory only; a bot restart starts fresh threads.
- **Failure handling:** `ask_claude` returns `None` on any failure (rc≠0, timeout, bad
  JSON), and `handle_message` falls through to the existing OpenRouter loop → Antigravity
  fallback, which are left untouched.
- **Photo path unchanged:** `handle_photo` still uses OpenRouter vision models.

## Architecture / Data Flow

```
Telegram msg → handle_message
  → ask_claude(chat_id, text)  [run_in_executor, off the event loop]
      → claude -p --output-format json --model sonnet
               --mcp-config concierge_mcp.json --strict-mcp-config
               --allowedTools mcp__concierge --disallowedTools Bash Write Edit
               [--resume <session_id>]
      → claude drives mcp__concierge__* tools (concierge_server.py → tools.py funcs)
      → returns {result, session_id}; session cached per chat
  → reply sent (chunked to 4000 chars)
  → on None: fall through to OpenRouter free models → Antigravity (unchanged)
```

The MCP server is a ~70-line raw JSON-RPC stdio server mirroring
`mcp-servers/calendar_server.py`. Tool schemas come from the shared canonical source
`telegram-bot/tool_specs.py`, which also feeds the OpenRouter path — one definition, two
formats (`openai_tools()` / `mcp_tools()`) plus a dispatch map (`func_map()`).

## File List

**New:**
- `telegram-bot/tool_specs.py` — canonical tool defs (SPECS + openai_tools/mcp_tools/func_map)
- `mcp-servers/concierge_server.py` — stdio MCP server wrapping the bot's tools
- `telegram-bot/concierge_mcp.json` — MCP config (concierge server only)
- `telegram-bot/claude_backend.py` — `ask_claude()` with session caching + fallback signal
- Tests: `test_tool_specs.py`, `test_concierge_server.py`, `test_claude_backend.py`

**Modified:**
- `telegram-bot/bot.py` — import tools from `tool_specs`; `ask_claude` primary path in
  `handle_message`; `asyncio` import
- `telegram-bot/test_bot.py` — patch `bot.ask_claude` in fallback tests; new
  claude-primary happy-path test

## Notes / Trade-offs

- Latency: each reply spawns the CLI + MCP server (~2–6s on Sonnet). Acceptable for a
  personal bot; "typing" indicator covers it.
- `--dangerously-skip-permissions` is used (repo norm; avoids permission-prompt hangs in
  the headless systemd service). `--strict-mcp-config` + scoped `--allowedTools` +
  `--disallowedTools Bash Write Edit` keep it on-task; built-in tools remain technically
  reachable but the system prompt + scoping keep behaviour focused.
- Verified end-to-end: CLI loads the concierge server, calls `get_plant_status`, returns
  a formatted answer with no permission denials; `--resume` preserves context.
