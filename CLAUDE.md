# Project Context

Personal AI agent workspace for automating day-to-day tasks and learning AI automation.

# Multi-LLM Setup
- This codebase is worked on by both **Claude Code** (codename: eagna) and **Gemini CLI**
- Gemini picks up work when Claude hits rate limits, and vice versa
- **CLAUDE.md is the single source of truth** — both agents read it. Keep it updated with:
  - New files, agents, or scripts added to the project
  - New conventions or architectural decisions
  - In-progress plans or migration states
  - Any non-obvious context a fresh session would need
- GEMINI.md defers to CLAUDE.md (do not duplicate rules there)

# Token Efficiency Rules
- **Be terse.** Short answers, no filler, no restating what I said.
- **Don't explore what's documented here.** This file IS the map — don't glob/grep to rediscover project structure.
- **Don't read skill files unless editing them.** The summaries below are enough for context.
- **Don't read MCP server source unless debugging them.** Tool names are discoverable via MCP; the code is auth/plumbing.
- **Skip preamble.** No "Let me...", "I'll now...", "Great question!" — just do the thing.
- **One-shot edits.** Read a file once, make all changes, move on. Don't re-read to verify unless the edit is high-risk.
- **Minimise subagent use.** Only spawn agents for genuinely parallel or deep-search tasks. Direct tool calls are cheaper.

# General Rules
- Ask clarifying questions before starting a complex task
- Show plan and steps before executing
- **Always write a design doc** to `docs/` for any non-trivial feature or change before or after implementation. Include: problem, design decisions, architecture, data model, and file list. This is mandatory — it ensures context survives across sessions and LLM switches.
- Keep reports and summaries concise — bullet points over paragraphs
- Save all output files to the `output/` folder
- Cite sources when doing research

# Project Structure
```
├── agents/             # Python agent framework (replacing workflows)
│   ├── base.py                 # BaseAgent class — lifecycle, retry, state, LLM failover
│   ├── db.py                   # SQLite wrapper (AgentDB)
│   ├── runner.py               # CLI: python3 -m agents <command>
│   ├── weather.py              # Open-Meteo weather client (Leiden default, no API key)
│   ├── plant_weather.py        # Weather-based watering adjustment logic (pure functions)
│   ├── daily_briefing.py       # Daily briefing agent (schedule: 05:05 UTC / 07:05 CEST)
│   ├── news_briefing.py        # News briefing agent (schedule: 05:00 UTC / 07:00 CEST)
│   ├── security_audit.py       # Security audit agent — 18 checks: 12 system + 4 seedbox + 2 web (Cloudflare IP validation, Shodan InternetDB). Schedule: Sunday 06:00 UTC / 08:00 CEST. Seedbox configs live in ~/git/yopflix (private repo).
│   └── prompts/                # LLM CLI synthesis prompt templates
│       ├── daily_briefing.md
│       └── news_briefing.md
├── data/               # SQLite database (gitignored)
├── output/             # Finished deliverables (reports, drafts, analysis)
├── scripts/            # Shell helper scripts (e.g. Google token check)
├── skills/             # Claude Code custom skills
│   ├── mealsave/               # Save recipes to Mealie instance
│   └── free-time/              # Suggest best tasks for a free time window
├── mcp-servers/        # Custom MCP servers (calendar, gmail auth)
├── tests/              # pytest test suite (run: pytest tests/)
│   ├── test_synthesize.py      # Failover + prompt adaptation tests
│   ├── test_weather.py         # Open-Meteo weather fetch tests
│   ├── test_plant_weather.py   # Watering adjustment logic tests
│   ├── test_daily_briefing.py  # Daily briefing integration tests
│   └── test_security_audit.py  # Cloudflare IP + Shodan exposure tests
├── docs/               # Design docs (mandatory for non-trivial changes)
│   ├── llm-failover.md         # Claude→Gemini failover design doc
│   └── weather-aware-plant-watering.md  # Weather-based watering adjustments
├── free_time_bot.py    # Telegram bot: free-time task advisor (systemd service)
├── plant.sh            # CLI tool: manage plant watering tracker (add/list/remove)
├── run-agent.sh        # Single entrypoint for all agents
└── credentials.json    # Google OAuth credentials (DO NOT commit secrets)
```

# Agent Conventions
- Agents are Python classes in `agents/` extending `BaseAgent`
- **Dual-CLI rule:** All agent Python code must be runnable by both Claude and Gemini CLI. No Claude-specific or Gemini-specific dependencies in Python. LLM-specific adaptations happen in `BaseAgent.synthesize()` only.
- Execution model: Python handles lifecycle, state (SQLite), retry, dedup, and deterministic logic (e.g. plant watering, weather). LLM CLI (with MCP tools) handles data fetching, formatting, and email sending. Exception: `security_audit.py` is mostly deterministic (subprocess + web APIs for Cloudflare/Shodan checks, no LLM CLI or MCP).
- **LLM failover:** `BaseAgent.synthesize()` tries Claude CLI first, falls back to Gemini CLI on infrastructure failure (rate limits, timeouts, quota). Prompts are adapted at runtime for Gemini (tool name remapping, ToolSearch stripping, WebFetch→curl). See `docs/llm-failover.md` for details.
- Run via: `run-agent.sh <agent-name>` or `python3 -m agents <agent-name>`
- Each agent declares its own cron schedule; `python3 -m agents install-cron` writes crontab entries
- Agent state lives in `data/agents.db` (SQLite) — never store secrets there
- MCP servers (Todoist, Calendar, Gmail) are used via LLM CLI (Claude or Gemini), not called directly from Python. Both CLIs have identical MCP server configs — Claude via `.mcp.json`, Gemini via `gemini mcp add` (project scope).
- Step failure handling: if a step exhausts retries, execution continues but the run is marked `partial_failure` in the DB (not `success`). Check `_failed_steps` in `report()` if you need to adjust output.
- Agents log operational notes (feed failures, API quirks, unexpected behavior) to `docs/agent-notes.md` (gitignored) to save tokens on future runs
- All cron schedules target 7:00 AM Amsterdam time (CEST = UTC+2, so 05:00 UTC)

# Skills
- Skills are Claude Code interactive commands in `skills/<name>/SKILL.md`
- `mealsave` — save recipe URLs to Mealie (`/mealsave <url>`)
- `free-time` — suggest best tasks for a free time window ("I have 30 minutes free")

# Plant Watering Tracker
- Data lives in `data/agents.db` (SQLite state table); CLI tool is `plant.sh` (add/list/remove/--outdoor)
- Plant data model: `{name, frequency_days, last_watered, location}` — location is `"indoor"` or `"outdoor"`
- **Weather-aware:** Daily briefing fetches weather from Open-Meteo (Leiden) and adjusts watering dates automatically:
  - Indoor: ±1-2 days based on temp/humidity (subtle)
  - Outdoor: ±1-3 days based on rain, heatwaves, dry spells (larger adjustments)
  - Weather fetch failure is non-fatal — falls back to base schedule
- Daily briefing agent checks plants and creates Todoist reminders automatically
- **When adding a plant without an explicit frequency:** search the web for recommended indoor watering cadence, check at least 3 sources, and use the consensus value. Do NOT default to 7 days.

# Available MCP Integrations
Configured for both Claude (`.mcp.json`) and Gemini (`gemini mcp` project scope):
- **Todoist** — remote HTTP MCP (`ai.todoist.net/mcp`). Task management (find-tasks, add-tasks, complete-tasks, etc.)
- **Google Calendar** — local stdio MCP (`mcp-servers/calendar_server.py`). Event listing, creation, scheduling
- **Gmail** — local stdio MCP (`mcp-servers/gmail_server.py`). Search threads, send/draft emails, label management
- **Google Drive** — file access (authenticated via OAuth, Claude-native only)