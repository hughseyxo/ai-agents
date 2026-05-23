# Project Context

Personal AI agent workspace for automating day-to-day tasks and learning AI automation.

# Multi-LLM Setup
- This codebase is worked on by both **Claude Code** (codename: eagna) and **Gemini CLI**
- **Gemini is the primary agent** for all automated tasks to mitigate Claude usage spikes.
- Claude acts as a fallback if Gemini fails (timeouts, logic errors, etc.).
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
│   ├── base.py                 # BaseAgent class — lifecycle, retry, state, LLM failover. Set `providers` class attr to override provider order per-agent.
│   ├── db.py                   # SQLite wrapper (AgentDB)
│   ├── runner.py               # CLI: python3 -m agents <command>
│   ├── weather.py              # Open-Meteo weather client (Leiden default, no API key)
│   ├── plant_weather.py        # Weather-based watering adjustment logic (pure functions)
│   ├── daily_briefing.py       # Daily briefing agent (schedule: 04:05 UTC / 06:05 CEST, model: claude-sonnet-4-6)
│   ├── news_briefing.py        # News briefing agent (schedule: 04:00 UTC / 06:00 CEST, model: claude-haiku-4-5, Claude primary). Steps: fetch_news → translate_dutch (NOS Binnenland articles) → news_briefing (send email). HTML/markdown pre-built in Python; LLM only sends email. Sources: BBC, RTE/TheJournal/Irish Times(Google News), DutchNews/NLTimes/NOS(Dutch→translated), Leiden/Mullingar(Google News), Verge/TC/HN/ARS/Register, Polygon, HN SRE.
│   ├── security_audit.py       # Security audit agent — 18 checks: 12 system + 4 seedbox + 2 web (Cloudflare IP validation, Shodan InternetDB). Schedule: Sunday 06:00 UTC / 08:00 CEST. Seedbox configs live in ~/git/yopflix (private repo).
│   ├── commit_security.py      # Commit security agent (on-demand) — LLM-based scan of git diff for secrets/vulnerabilities. run_hook() used by .git/hooks/pre-push; blocks push on Critical/High. Also runnable via CLI.
│   ├── travel_agent.py         # Travel agent (on-demand) — search mode: finds flights/hotels/activities; plan mode: itinerary from existing bookings. model: claude-sonnet-4-6. Design doc: docs/travel-agent.md
│   ├── librarian.py            # Librarian agent (on-demand, cron-managed: audit Sun 06:00 UTC, watch Mon-Sat 06:00 UTC). Reads agent run history + output files, calls LLM to produce findings. Auto-applies learnings (confidence ≥0.8), emails prompt proposals (0.5-0.79) with approve/reject links via bridge server.
│   └── prompts/                # LLM CLI synthesis prompt templates
│       ├── daily_briefing.md
│       ├── news_briefing.md
│       ├── commit_security.md       # Prompt for commit diff security analysis
│       ├── travel_agent_search.md   # Search mode: find flights, hotels, activities
│       ├── travel_agent_plan.md     # Plan mode: day-by-day itinerary from existing bookings
│       ├── librarian_audit.md       # Weekly full analysis prompt
│       ├── librarian_watch.md       # Daily failure-scan prompt
│       └── librarian_report.md      # Email send prompt
├── telegram-bot/       # Server concierge Telegram bot (OpenRouter-backed)
│   ├── bot.py                  # Bot: polling, auth gate, tool-use loop, model fallback
│   ├── tools.py                # Tool functions: get_agent_status, get_plant_status, get_yopflix_status, get_system_health, get_cron_schedule, get_agent_logs, queue_tiktok
│   ├── concierge-bot.service   # systemd user service (symlinked to ~/.config/systemd/user/)
│   ├── test_bot.py             # Bot handler tests (auth, tool-use loop)
│   ├── test_tools.py           # Tool function unit tests (mocked deps)
│   ├── .env                    # Private API keys (gitignored): TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY, TELEGRAM_USER_ID
│   └── .env.example            # Template for setup
├── data/               # SQLite database (gitignored)
├── output/             # Finished deliverables (reports, drafts, analysis)
├── scripts/            # Shell helper scripts (e.g. Google token check)
├── skills/             # Claude Code custom skills
│   ├── mealsave/               # Save recipes to Mealie instance
│   │   ├── mealsave.py         # Main extraction logic (Schema.org, YouTube, TikTok, Trafilatura)
│   │   ├── mealsave_bot.py     # Telegram bot for remote saving
│   │   └── check-yt-auth.sh    # YouTube cookie expiry check
│   └── free-time/              # Suggest best tasks for a free time window
├── mcp-servers/        # Custom MCP servers (calendar, gmail auth) + bridge_server.py (HTTP MCP over Tailscale for laptop access). GET /librarian/approve?id=&token= and /librarian/reject?id=&token= for one-click proposal approval.
├── triggers/           # Agent operation docs — how to run agents on demand via the MCP bridge from laptop Claude Code. See triggers/README.md.
├── tests/              # pytest test suite (run: pytest tests/)
│   ├── test_synthesize.py      # Failover + prompt adaptation + providers override tests
│   ├── test_news_briefing.py   # RSS parsing, dedup, Dutch translation, HTML/markdown builder tests
│   ├── test_weather.py         # Open-Meteo weather fetch tests
│   ├── test_plant_weather.py   # Watering adjustment logic tests
│   ├── test_daily_briefing.py  # Daily briefing integration tests
│   ├── test_security_audit.py  # Cloudflare IP + Shodan exposure tests
│   └── test_mealsave_tiktok.py # TikTok caption metadata fetch tests
├── docs/               # Design docs (mandatory for non-trivial changes)
│   ├── llm-failover.md         # Claude→Gemini failover design doc
│   ├── weather-aware-plant-watering.md  # Weather-based watering adjustments
│   ├── mealsave-tiktok-and-telegram.md  # TikTok OCR + Telegram bot design
│   ├── hermes-evaluation.md    # Summary of failed CLI-proxy/local-inference efforts
│   ├── openrouter-telegram-bot.md # OpenRouter bot architecture (superseded by concierge)
│   ├── telegram-bots.md        # Overview of all Telegram bots, status, failure history
│   ├── mcp-bridge.md           # MCP bridge server design (Tailscale HTTP, 7 tools)
│   ├── travel-agent.md         # Travel agent design (search + plan modes, no API keys)
│   └── superpowers/
│       ├── specs/
│       │   └── 2026-05-17-server-concierge-bot-design.md  # Concierge bot spec
│       └── plans/
│           └── 2026-05-17-laptop-server-bridge.md  # Laptop↔server bridge implementation plan
├── skills/vidqueue/    # YouTube video essay queue from TikTok recommendations
│   ├── SKILL.md            # Skill definition (trigger: /vidqueue <url>)
│   ├── vidqueue.py         # Core: TikTok extraction + YouTube Data API playlist management
│   └── requirements.txt    # Python deps (yt-dlp, whisper, pytesseract, google-api-python-client)
├── free_time_bot.py    # Telegram bot: free-time task advisor (systemd service)
├── mealsave-bot.service # Telegram bot: recipe saver (systemd service)
├── plant.sh            # CLI tool: manage plant watering tracker (add/list/remove)
├── run-agent.sh        # Single entrypoint for all agents
└── credentials.json    # Google OAuth credentials (DO NOT commit secrets)
```

# Agent Conventions
- Agents are Python classes in `agents/` extending `BaseAgent`
- **Dual-CLI rule:** All agent Python code must be runnable by both Claude and Gemini CLI. No Claude-specific or Gemini-specific dependencies in Python. LLM-specific adaptations happen in `BaseAgent.synthesize()` only.
- Execution model: Python handles lifecycle, state (SQLite), retry, dedup, and deterministic logic (e.g. plant watering, weather, RSS fetching). LLM CLI (with MCP tools) handles data synthesis, formatting, and email sending.
- **Model selection:** Set `model = "claude-sonnet-4-6"` (or haiku/opus) on the agent class. `BaseAgent.synthesize()` maps these to appropriate Gemini models when using Gemini, or passes them directly to Claude.
- **Per-agent provider override:** Set `providers = [...]` on the agent class to change provider order. Default is Gemini-first; `news_briefing` overrides to Claude-first. See `BaseAgent.PROVIDERS` for the full list.
- **LLM failover & Timeouts:** `BaseAgent.synthesize()` tries providers in order, adapting prompts for Gemini at runtime.
  - **Timeouts:** A 600s timeout is applied to LLM calls. If a step marked with `side_effects: True` times out, the agent skips retries to avoid duplicate actions (e.g. sending multiple emails).
- **On-demand agents:** Set `schedule = ""` — they won't appear in crontab but can be triggered manually or via Telegram bot. Pass extra args via `configure(args)` called by `cmd_run` in runner.py.

# Security & Git Workflow
- **Security Audit Before Push:** Gemini MUST run the security audit agent (`run-agent.sh security-audit`) before every `git push` to a public branch. If any "Critical" or "High" severity findings are found in unpushed or staged changes (Check 16), the push MUST be aborted until fixed or explicitly exempted by the user.
- **Atomic Commits:** Prefer small, focused commits with clear descriptions.
- **No Secrets:** Never commit `.env`, `credentials.json`, or any files containing API keys or private data.
- **Media Files:** Do not commit `.mp4`, `.mkv`, or other large media files downloaded by agents.

- Run via: `run-agent.sh <agent-name>` or `python3 -m agents <agent-name>`
- Each agent declares its own cron schedule; `python3 -m agents install-cron` writes crontab entries
- Agent state lives in `data/agents.db` (SQLite) — never store secrets there
- MCP servers (Todoist, Calendar, Gmail) are used via LLM CLI (Claude or Gemini), not called directly from Python. Both CLIs have identical MCP server configs — Claude via `.mcp.json`, Gemini via `gemini mcp add` (project scope).
- Step failure handling: if a step exhausts retries, execution continues but the run is marked `partial_failure` in the DB (not `success`). Check `_failed_steps` in `report()` if you need to adjust output.
- Agents log operational notes (feed failures, API quirks, unexpected behavior) to `docs/agent-notes.md` (gitignored) to save tokens on future runs
- All cron schedules target 6:00 AM Amsterdam time (CEST = UTC+2, so 04:00 UTC)

# Skills
- Skills are Claude Code interactive commands in `skills/<name>/SKILL.md`
- `mealsave` — save recipe URLs to Mealie (`/mealsave <url>`)
- `free-time` — suggest best tasks for a free time window ("I have 30 minutes free")
- `vidqueue` — extract YouTube video essay recommendations from a TikTok URL and add to a YouTube playlist (`/vidqueue <url>`). Mobile: send TikTok link to the concierge Telegram bot (uses `queue_tiktok` tool). Core logic: `skills/vidqueue/vidqueue.py`. Config: `~/.config/vidqueue/.env`. Auth bootstrap: `vidqueue.py --auth`.

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

# Server Concierge Telegram Bot
- **Role:** Natural-language interface to query server status. Ask about agent runs, plant watering, yopflix/seedbox, system health, cron schedules, and logs.
- **Backend:** OpenRouter free tier with model fallback: `deepseek/deepseek-chat-v3-0324:free` → `google/gemma-3-27b-it:free` → `meta-llama/llama-3.3-70b-instruct:free`
- **Auth:** `TELEGRAM_USER_ID` env var — all other users silently ignored
- **Tool use:** LLM calls tool functions in `tools.py` to fetch live data; results injected back into conversation (max 3 tool-use iterations per message)
- **Service:** `concierge-bot.service` (systemd user service, `~/.config/systemd/user/`)
- **Note:** OpenRouter free models are the only viable LLM backend — Anthropic/Google API keys require separate paid API plans, not covered by Pro subscriptions
- **Design doc:** `docs/superpowers/specs/2026-05-17-server-concierge-bot-design.md`
