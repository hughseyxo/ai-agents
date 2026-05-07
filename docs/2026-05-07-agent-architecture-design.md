# Agent Architecture: Migrate Workflows to Autonomous Agents

## Context

The repo currently has markdown "workflow" files executed headlessly via `claude -p`. These work but lack error recovery, state persistence, dedup, and dynamic behavior. The goal is to migrate workflows into Python agents that handle data fetching, state, retry, and dedup in Python, then pass collected data to Claude CLI for formatting and synthesis. SQLite provides operational state. Skills (interactive Claude Code commands) remain unchanged.

**Execution model:** All agents use Python for data plumbing + Claude CLI for synthesis/formatting. Python fetches the data, handles retries and fallbacks, manages state in SQLite. Claude CLI receives the collected data as a prompt and produces the formatted output (markdown report, HTML email). This keeps LLM flexibility for both briefing agents.

**First migration target:** Daily briefing (most complex, benefits most from state/recovery).

**Key constraint:** No duplicate systems — workflows become agents, not a parallel layer.

---

## Phase 1: Foundation

Create the agent framework with no behavior change to existing workflows.

### 1.1 Directory setup
- Create `agents/` with `__init__.py`
- Create `data/` directory, add to `.gitignore`

### 1.2 `agents/db.py` — SQLite wrapper
Schema tables:
- **runs** — agent run history (id, agent, started_at, finished_at, status, output_summary, error)
- **steps** — per-step results within a run (run_id, step, status, error, ts)
- **state** — key-value per agent, JSON-encoded values (replaces flat files like plants.json)
- **seen** — dedup tracking (agent, category, identifier, first_seen)

Class `AgentDB` with methods: `start_run()`, `complete_run()`, `record_step()`, `get_state()`, `set_state()`, `check_dedup()`, `mark_seen()`, `get_last_run()`, `get_run_history()`.

### 1.3 `agents/base.py` — BaseAgent class
Lifecycle: `pre_check -> plan -> execute steps -> report -> record`

- `steps()` returns list of `{'name', 'fn', 'fallback?', 'retries?'}` dicts
- `_execute_step()` handles retry + fallback per step
- Failed steps set `self.context[name] = None` and continue (resilient)
- `plan()` hook for dynamic decisions (optional override)
- State/dedup helpers: `get_state()`, `set_state()`, `is_duplicate()`, `mark_seen()`
- `schedule` attribute — cron expression declaring when agent should run

### 1.4 `mcp-servers/google_auth.py` — extract shared OAuth
Lines 15-54 of `calendar_server.py` and `gmail_server.py` are identical. Extract to shared module:
- `load_tokens()`, `save_tokens()`, `get_access_token()`, `refresh_google_token()`
- Refactor both MCP servers to `from google_auth import get_access_token`

### 1.5 `agents/runner.py` — CLI entrypoint
```
python3 -m agents.runner daily-briefing     # run one agent
python3 -m agents.runner --list             # list agents + schedules
python3 -m agents.runner --install-cron     # write crontab entries
python3 -m agents.runner --history <agent>  # recent runs
```

### 1.6 `run-agent.sh` — single shell entrypoint
Replaces all `run-*.sh`. Sources `.env`, exports tokens, calls `python3 -m agents.runner "$@"`.

---

## Phase 2: Daily Briefing Agent

### 2.1 `agents/daily_briefing.py` — Python data fetching + Claude CLI formatting

**Data steps (Python):**
1. `calendar_today` — fetch today's events via Google Calendar API (reuse `gcal_request()` pattern from `calendar_server.py`)
2. `calendar_upcoming` — fetch next 30 days from primary + on-call calendars
3. `todoist_inbox` — fetch inbox tasks via Todoist REST API (`GET /rest/v2/tasks?project_id=inbox`), fallback already built into step dict
4. `todoist_upcoming` — fetch tasks by date range via Todoist REST API
5. `plants` — read plant data from `state` table, calculate next_water_date, create Todoist tasks if needed via REST API, update state

**Synthesis step (Claude CLI):**
6. `synthesize` — pass all collected data as JSON to Claude CLI with a formatting prompt. Claude produces:
   - Markdown report (saved to `output/daily-briefing-YYYY-MM-DD.md`)
   - HTML email (inline CSS, same design as current workflow)
   - Quick wins estimation, prioritization, any dynamic section adjustments

**Delivery step (Python):**
7. `send_email` — send HTML email via Gmail API (reuse `gmail_request()` pattern from `gmail_server.py`)
   - Dedup: `self.mark_seen('email_sent', today())` prevents double-sends

**Key difference from workflow:** Python handles all API calls, retry, fallback, and state. Claude CLI receives clean structured data and focuses purely on formatting and judgment calls. No ToolSearch, no MCP discovery overhead.

### 2.2 Synthesis prompt template
Store as `agents/prompts/daily_briefing.md` — a prompt template that receives JSON data and instructions for formatting. This replaces the current `workflows/daily-briefing.md` but is much shorter (just formatting rules + HTML template, no data fetching instructions).

### 2.3 Port Google API helpers
Create `agents/google.py` — thin wrappers importing from `mcp-servers/google_auth.py`:
- `fetch_calendar_events(calendar_id, start, end, tz)` — wraps `gcal_request()`
- `send_gmail(to, subject, html_body)` — wraps `gmail_request()`

### 2.4 Port Todoist API helpers
Create `agents/todoist.py`:
- `fetch_tasks(project_id=None, filter=None)` — `GET /rest/v2/tasks`
- `fetch_tasks_by_date(start, days)` — date range query
- `create_task(content, project_id, due_string, priority)` — `POST /rest/v2/tasks`
- Uses `TODOIST_API_TOKEN` from env

### 2.5 Test and swap
- Run `run-agent.sh daily-briefing` and compare output to existing workflow
- Update crontab via `run-agent.sh --install-cron`
- Deprecate `run-daily-briefing.sh` (keep briefly, then delete)

---

## Phase 3: Plant State Migration

1. One-time migration: read `~/plants.json` -> insert into `state` table as `agent='daily-briefing', key='plants'`
2. Update `DailyBriefingAgent` to read/write SQLite only
3. Update `plant.sh` to read/write SQLite via `sqlite3` CLI commands (or rewrite as `python3 -m agents.plants`)
4. Drop `~/plants.json` dependency

---

## Phase 4: News Briefing Agent (future, not this session)

- Python for RSS fetching + retry + dedup via `seen` table
- Claude CLI for article selection/summarization (needs LLM judgment)
- Same pattern: `agents/news_briefing.py` extending `BaseAgent`

---

## Phase 5: Cleanup (after both agents migrated)

- Delete `workflows/` directory
- Delete old `run-*.sh` scripts
- Update `CLAUDE.md` with new project structure

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `agents/__init__.py` | Create | Package init |
| `agents/db.py` | Create | SQLite schema + AgentDB class |
| `agents/base.py` | Create | BaseAgent lifecycle |
| `agents/runner.py` | Create | CLI entrypoint |
| `agents/daily_briefing.py` | Create | First agent |
| `agents/prompts/daily_briefing.md` | Create | Formatting prompt template for Claude CLI |
| `agents/google.py` | Create | Calendar + Gmail API wrappers |
| `agents/todoist.py` | Create | Todoist REST API wrapper |
| `mcp-servers/google_auth.py` | Create | Extracted shared OAuth |
| `mcp-servers/calendar_server.py` | Modify | Import from google_auth |
| `mcp-servers/gmail_server.py` | Modify | Import from google_auth |
| `run-agent.sh` | Create | Single entrypoint |
| `data/.gitkeep` | Create | SQLite DB directory |
| `.gitignore` | Modify | Add `data/agents.db` |
| `CLAUDE.md` | Modify | Updated project structure |

---

## Security Notes
- SQLite stores only operational state (run history, dedup hashes, plant schedules)
- **Never** store API tokens, OAuth secrets, or credentials in SQLite
- Tokens remain in `.env` and `~/.google_tokens.json` with 600 permissions
- Todoist API token read from env var at runtime, never persisted in agent code

---

## Verification
1. `python3 -m agents.runner --list` shows daily-briefing with schedule `5 7 * * *`
2. `run-agent.sh daily-briefing` produces `output/daily-briefing-YYYY-MM-DD.md` matching current workflow output
3. Email arrives with same HTML format
4. `python3 -m agents.runner --history daily-briefing` shows the run with status=success
5. `plant.sh list` still works (reads from SQLite after migration)
6. Run twice in a row — second run doesn't send duplicate email (dedup check)
7. Kill network mid-run — agent records step failures, continues, reports partial results

---

## Session Continuity Note
If this session runs out of tokens, resume with: "Continue implementing the agent architecture from docs/2026-05-07-agent-architecture-design.md". The plan file is also at `.claude/plans/encapsulated-juggling-wren.md`.
