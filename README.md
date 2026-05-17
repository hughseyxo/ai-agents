# AI Agents

Personal AI agent workspace running on an OVH VPS. Automates daily tasks via scheduled Python agents that use Claude (with Gemini fallback) as the reasoning layer.

## What's running

| Agent | Schedule | What it does |
|---|---|---|
| `daily-briefing` | 07:05 CEST daily | Calendar, Todoist tasks, plant watering reminders → HTML email |
| `news-briefing` | 07:00 CEST daily | RSS feeds synthesised into a digest → email |
| `security-audit` | Sunday 08:00 CEST | 18 checks: SSH, firewall, Docker, Traefik, Cloudflare, Shodan |

Agents are Python classes extending `BaseAgent` in `agents/`. Python handles scheduling, state (SQLite), retry, and deduplication. Claude CLI (with MCP tools) handles synthesis, formatting, and sending.

## Architecture

```
agents/
├── base.py            # BaseAgent: lifecycle, retry, LLM failover
├── db.py              # SQLite wrapper
├── daily_briefing.py  # Daily briefing agent
├── news_briefing.py   # News briefing agent
└── security_audit.py  # Security audit agent

telegram-bot/
└── bot.py             # Concierge bot: natural-language server status queries

mcp-servers/
└── bridge_server.py   # HTTP MCP server (Tailscale-only) for laptop access
```

## Telegram Bot

A concierge bot (`telegram-bot/`) provides natural-language access to server status. Ask it about agent runs, plant watering, system health, cron schedules, or logs. Backed by OpenRouter free models with automatic fallback.

## Laptop Access (MCP Bridge)

An HTTP MCP server runs on the Tailscale interface, letting a laptop Claude Code session control the server directly:

```
list_agents()                                                    # last run status
get_agent_status("daily-briefing")                              # step-level history
exec_shell("bash run-agent.sh daily-briefing", working_dir=...) # trigger agent
read_file("/home/cian/...") / write_file(...) / list_directory(...)
```

See [`triggers/README.md`](triggers/README.md) for usage and [`docs/mcp-bridge.md`](docs/mcp-bridge.md) for architecture.

## Running agents

```bash
# Single agent
bash run-agent.sh daily-briefing
bash run-agent.sh news-briefing
bash run-agent.sh security-audit

# Or via Python module
python3 -m agents daily-briefing

# Install cron entries
python3 -m agents install-cron
```

## Plant Watering Tracker

Plants are tracked in SQLite with weather-aware scheduling (Open-Meteo, Leiden). The daily briefing creates Todoist reminders automatically.

```bash
./plant.sh add "Monstera" --frequency 7
./plant.sh list
./plant.sh remove "Monstera"
```

## MCP Integrations

Configured for both Claude (`.mcp.json`) and Gemini (`gemini mcp`):

- **Todoist** — task management
- **Google Calendar** — event listing and creation
- **Gmail** — send/draft/search emails

## Tests

```bash
pytest tests/
```

## Skills

Custom Claude Code commands in `skills/`:

- `/mealsave <url>` — save a recipe URL to Mealie
- Free-time advisor — suggest tasks for a time window

## Docs

Design docs in [`docs/`](docs/) — required for any non-trivial change.

## Security

The security audit agent runs before every push to a public branch. Critical or High findings in staged changes block the push until resolved.
