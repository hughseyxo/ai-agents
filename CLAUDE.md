# Project Context

Personal AI agent workspace for automating day-to-day tasks and learning AI automation.

# Token Efficiency Rules
- **Be terse.** Short answers, no filler, no restating what I said.
- **Don't explore what's documented here.** This file IS the map — don't glob/grep to rediscover project structure.
- **Don't read workflow files unless editing them.** The summaries below are enough for context.
- **Don't read MCP server source unless debugging them.** Tool names are discoverable via MCP; the code is auth/plumbing.
- **Skip preamble.** No "Let me...", "I'll now...", "Great question!" — just do the thing.
- **One-shot edits.** Read a file once, make all changes, move on. Don't re-read to verify unless the edit is high-risk.
- **Minimise subagent use.** Only spawn agents for genuinely parallel or deep-search tasks. Direct tool calls are cheaper.

# General Rules
- Ask clarifying questions before starting a complex task
- Show plan and steps before executing
- Keep reports and summaries concise — bullet points over paragraphs
- Save all output files to the `output/` folder
- Cite sources when doing research

# Project Structure
```
├── agents/             # Python agent framework (replacing workflows)
│   ├── base.py                 # BaseAgent class — lifecycle, retry, state
│   ├── db.py                   # SQLite wrapper (AgentDB)
│   ├── runner.py               # CLI: python3 -m agents <command>
│   ├── daily_briefing.py       # Daily briefing agent (schedule: 05:05 UTC / 07:05 CEST)
│   ├── news_briefing.py        # News briefing agent (schedule: 05:00 UTC / 07:00 CEST)
│   └── prompts/                # Claude CLI synthesis prompt templates
│       ├── daily_briefing.md
│       └── news_briefing.md
├── workflows/          # Legacy workflow files (reference only, migrated to agents)
│   └── free-time-advisor.md    # Suggests activities based on schedule/weather (not yet migrated)
├── data/               # SQLite database (gitignored)
├── output/             # Finished deliverables (reports, drafts, analysis)
├── scripts/            # Shell helper scripts (e.g. Google token check)
├── skills/             # Claude Code custom skills (e.g. mealsave)
├── mcp-servers/        # Custom MCP servers (calendar, gmail auth)
├── docs/               # Design docs and architecture specs
├── plant.sh            # CLI tool: manage plant watering tracker (add/list/remove)
├── run-agent.sh        # Single entrypoint for all agents
└── credentials.json    # Google OAuth credentials (DO NOT commit secrets)
```

# Agent Conventions
- Agents are Python classes in `agents/` extending `BaseAgent`
- Execution model: Python handles lifecycle, state (SQLite), retry, dedup, and deterministic logic (e.g. plant watering). Claude CLI (with MCP tools) handles data fetching, formatting, and email sending.
- Run via: `run-agent.sh <agent-name>` or `python3 -m agents <agent-name>`
- Each agent declares its own cron schedule; `python3 -m agents install-cron` writes crontab entries
- Agent state lives in `data/agents.db` (SQLite) — never store secrets there
- MCP servers (Todoist, Calendar, Gmail) are used via Claude CLI, not called directly from Python
- Agents log operational notes (feed failures, API quirks, unexpected behavior) to `docs/agent-notes.md` (gitignored) to save tokens on future runs
- All cron schedules target 7:00 AM Amsterdam time (CEST = UTC+2, so 05:00 UTC)

# Workflow Conventions (Legacy — mostly migrated)
- Daily briefing and news briefing are now agents — legacy workflow files kept as reference
- Only `free-time-advisor.md` remains unmigrated

# Plant Watering Tracker
- Data lives in `data/agents.db` (SQLite state table); CLI tool is `plant.sh` (add/list/remove)
- Daily briefing agent checks plants and creates Todoist reminders automatically
- **When adding a plant without an explicit frequency:** search the web for recommended indoor watering cadence, check at least 3 sources, and use the consensus value. Do NOT default to 7 days.

# Available MCP Integrations
- **Todoist** — task management (find-tasks, add-tasks, complete-tasks, etc.)
- **Google Calendar** — event listing, creation, scheduling
- **Gmail** — search threads, send/draft emails, label management
- **Google Drive** — file access (authenticated via OAuth)