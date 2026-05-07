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
├── workflows/          # Plain-english recipe files the agent follows
│   ├── daily-briefing.md       # Morning briefing: calendar + tasks + weather + news + plant watering
│   ├── news-briefing.md        # Combined news + tech + gaming + SRE briefing
│   └── free-time-advisor.md    # Suggests activities based on schedule/weather
├── output/             # Finished deliverables (reports, drafts, analysis)
├── scripts/            # Shell helper scripts (e.g. Google token check)
├── skills/             # Claude Code custom skills (e.g. mealsave)
├── mcp-servers/        # Custom MCP servers (calendar, gmail auth)
├── plant.sh            # CLI tool: manage plant watering tracker (add/list/remove)
├── run-*.sh            # Entrypoint scripts for scheduled agent runs
└── credentials.json    # Google OAuth credentials (DO NOT commit secrets)
```

# Workflow Conventions
- Workflows are markdown files in `workflows/` describing steps in plain English
- They reference MCP tools (Todoist, Google Calendar, Gmail) by name
- Entrypoint shell scripts (`run-*.sh`) invoke Claude Code headlessly with a workflow
- New workflows follow the same pattern: markdown recipe + shell entrypoint

# Plant Watering Tracker
- Data lives in `~/plants.json`; CLI tool is `plant.sh` (add/list/remove)
- Daily briefing checks plants and creates Todoist reminders automatically
- **When adding a plant without an explicit frequency:** search the web for recommended indoor watering cadence, check at least 3 sources, and use the consensus value. Do NOT default to 7 days.

# Available MCP Integrations
- **Todoist** — task management (find-tasks, add-tasks, complete-tasks, etc.)
- **Google Calendar** — event listing, creation, scheduling
- **Gmail** — search threads, send/draft emails, label management
- **Google Drive** — file access (authenticated via OAuth)