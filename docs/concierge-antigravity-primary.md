# Concierge Bot: Antigravity-Primary Backend

## Problem
- The concierge bot felt "specific on language" — when the primary path failed, it dropped to weak OpenRouter free models that need exact phrasing to behave, so replies were brittle.
- Project rule mandates **Antigravity primary** to spare Claude usage; the bot was the last holdout running Claude-first.

## Decision
- Flip the LLM backend chain to **Antigravity (agy CLI) → Claude (claude CLI)**.
- **Remove the OpenRouter free-model layer entirely.** `OPENROUTER_API_KEY` is no longer required by the bot.
- Multi-turn memory now survives only on the Claude secondary path (Antigravity path is stateless).

## agy CLI constraints
- **Invocation:** prompt is piped via **stdin** to `agy --dangerously-skip-permissions` (NOT `-p`).
- **System prompt:** no `--append-system-prompt` flag — system prompt is **prepended** to the prompt text.
- **MCP:** no per-call MCP isolation. Tools come from agy's **global** MCP config `~/.gemini/antigravity-cli/mcp_config.json`, which symlinks to `~/.gemini/config/mcp_config.json`. The `concierge` stdio server was added there.
- **Output:** no JSON output mode — plain text only.
- **Stateless:** plain-text output yields no session id to resume. `--continue` is unsafe because cron agents share agy's history, so each call is independent.
- **Trailer:** agy appends a "Summary of Work" trailer; suppressed via a prepended instruction in the prompt.

## Architecture / data flow
- `handle_message` → `ask_antigravity` (agy + concierge MCP) → on failure (rc≠0 / timeout / empty → returns None) → `ask_claude` fallback → on failure, error message to user.
- Tools execute **inside the concierge MCP server** (`mcp-servers/concierge_server.py`), driven by agy's native MCP tool use. `bot.py` no longer dispatches tool calls itself (the old OpenRouter tool-use loop is gone).
- Photo/vision path is **unchanged** — plant image analysis still runs on the claude CLI (Opus 4.8) via `claude_backend.assess_image()`.

## File list
- **New:** `telegram-bot/antigravity_backend.py` (`ask_antigravity`, `_run_agy`), mirroring `telegram-bot/claude_backend.py`.
- **New:** `telegram-bot/test_antigravity_backend.py`.
- **Modified:** `telegram-bot/bot.py` (chain flip; `_resolve_plant_name` now uses agy instead of OpenRouter free models), `telegram-bot/test_bot.py`, `telegram-bot/.env.example` (OPENROUTER_API_KEY removed).
- **Config:** `~/.gemini/config/mcp_config.json` (added `concierge` stdio server; symlinked from agy's global config path).

## Tradeoffs
- **No per-call MCP isolation:** the `concierge` tools are visible to all other agy/agent runs sharing the global MCP config (Claude path keeps `--strict-mcp-config` isolation).
- **Stateless primary:** no multi-turn memory on the Antigravity path; context only persists when a turn falls through to the Claude secondary.
