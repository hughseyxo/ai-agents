# Server Concierge Telegram Bot — Design Spec

**Date:** 2026-05-17  
**Status:** Approved

## Problem

The openrouter-bot (`telegram-bot/bot.py`) is a stateless general-purpose AI chat bot with no systemd service and no knowledge of the server. The user needs a single Telegram interface to query server state naturally: agent run history, plant watering schedules, seedbox/yopflix health, system vitals, cron schedules, and recent logs.

## Design Decisions

1. **Extend the existing openrouter-bot** rather than create a new bot. Same token, same process — fewer services to manage.
2. **LLM tool use** (OpenAI function calling via OpenRouter). User sends a natural message; the LLM decides which tools to call. Llama 3.3-70B supports function calling.
3. **Stateless per message.** No conversation history. Status queries are self-contained; the current state is always fetched fresh.
4. **Auth gate on TELEGRAM_USER_ID.** Silent ignore for other users.
5. **Tool logic in a separate module** (`tools.py`) so it can be unit-tested independently of the Telegram/OpenRouter machinery.
6. **psutil for system health**, pyyaml for seedbox config parsing.

## Architecture

```
User message
  → auth gate (TELEGRAM_USER_ID)
  → OpenRouter (Llama 3.3-70B) + 6 tool definitions
  → if tool_calls: execute locally → append results → loop (max 3 iterations)
  → final LLM text reply → Telegram
```

## Tools

| Function | Data source | Returns |
|---|---|---|
| `get_agent_status()` | `data/agents.db` via `AgentDB.get_last_run()` | Name, last run time, status, last error |
| `get_plant_status()` | `AgentDB.get_state('daily-briefing', 'plants')` | Plant name, next watering date, overdue flag |
| `get_yopflix_status()` | docker ps + df + `~/git/yopflix/seedbox/config.yaml` | Containers, enabled services, disk usage |
| `get_system_health()` | psutil | CPU%, RAM%, uptime |
| `get_cron_schedule()` | `crontab -l` | Agent → schedule → next run (human-readable) |
| `get_agent_logs(agent_name?)` | `output/cron.log` | Last 20 lines, filtered by agent if given |

## Bot persona (system prompt)

> You are a concierge assistant for Cian's home server. Answer questions about agents, plants, seedbox, and system health using your tools. Be concise and direct. Never guess current state — always use a tool to get live data.

## Files

| File | Change |
|---|---|
| `telegram-bot/bot.py` | Rewrite: tool-use loop, auth, concierge system prompt |
| `telegram-bot/tools.py` | New: 6 tool implementations |
| `telegram-bot/test_tools.py` | New: unit tests for tools (mocked dependencies) |
| `telegram-bot/test_bot.py` | Extend: auth gate + tool-use loop tests |
| `telegram-bot/concierge-bot.service` | New: systemd user service |
| `telegram-bot/.env` | Add `TELEGRAM_USER_ID` |
| `docs/telegram-bots.md` | Update: add concierge-bot entry |

## Data model

Plant records live in `agents.db`: `state` table, `agent='daily-briefing'`, `key='plants'`. Value is a JSON list of `{name, frequency_days, last_watered, location}`.

Agent run records in `runs` table: `agent`, `started_at`, `status` (`success`/`partial_failure`/`failure`/`running`), `error`.

## Error handling

Each tool returns a string. On any exception, the tool returns a short error string (e.g. `"docker unavailable: <reason>"`). The LLM relays it naturally to the user. No exceptions propagate to the bot handler.

## Dependencies added

- `psutil==7.2.2`
- `pyyaml==6.0.3`
