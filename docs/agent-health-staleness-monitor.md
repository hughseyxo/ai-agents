# Agent Health Staleness Monitor

## Problem
Agents are driven by crontab entries. If an entry is dropped (e.g. crontab
rewritten without an agent's line), the agent silently stops running — no error,
no alert. This happened to `plant-agent`: its hourly cron line went missing and
the agent was dead for ~3.5 days (no weather updates, watering sync, photo
requests, status emails, or intelligence runs) before anyone noticed. Nothing
surfaced the gap.

## Goal
Detect and surface stale/missing scheduled agents automatically, with low noise.

## Design decisions
- **Deterministic, no LLM.** Staleness is a pure time comparison; reliability
  matters more than flexibility. The monitor must not itself depend on an LLM
  that could fail.
- **Threshold = 2× the agent's own interval.** Derived from each agent's cron
  schedule, so an hourly agent flags after 2h and a daily agent after 2 days.
  Tolerates a single missed run; catches real failures fast.
- **Alert channel = Telegram push** (via the concierge bot). Push is immediate
  and the user already watches that chat. Reuses `CONCIERGE_BOT_TOKEN` /
  `TELEGRAM_USER_ID` (same path as plant-agent photo requests).
- **Hourly check cadence.** Needed so a 2h-stale hourly agent is caught promptly.
- **Deduplicated alerts.** A stale agent is alerted once (state-tracked), with a
  "healthy again" message on recovery — avoids hourly spam for the same outage.
- **Last *healthy* run** = most recent run with status `success` or
  `partial_failure`. A perpetually-erroring agent is also treated as stale.

## Architecture
`agents/agent_health.py` — `AgentHealthAgent(BaseAgent)`, `schedule = "0 * * * *"`.

Pure, unit-tested helpers (module-level):
- `cron_interval_seconds(schedule)` — estimates run interval from a 5-field cron
  expression (coarsest-specificity-first: specific DOW → weekly, specific hour →
  daily, `*/n` minute → n·60, specific minute + `*` hour → hourly, …).
- `evaluate_staleness(agents_state, now, factor=2)` — `agents_state` maps
  `name → (schedule, last_healthy_run_dt | None)`; returns sorted stale names
  (None last-run ⇒ stale).
- `diff_alerts(stale, previously_alerted)` — returns `(new_alerts, recovered)`.

Agent flow (single `check` step, `side_effects: True`):
1. `_monitored()` — iterate `AGENT_REGISTRY` (from `runner`), skip self and any
   agent with an empty `schedule` (on-demand / externally-managed like
   `librarian`, `travel-agent`), and read each one's last healthy run from the DB.
2. `evaluate_staleness(...)` → current stale set.
3. `diff_alerts(...)` against state key `alerted`; push Telegram alerts for new
   stale agents and recovery messages for recovered ones.
4. Persist the current stale set to state `alerted`.

Telegram creds come from `os.environ` (injected by `run-agent.sh` from the
repo-root `.env`) — **not** `telegram-bot/.env` (whose `TELEGRAM_BOT_TOKEN` is a
different, stale token; sending with it 404s).

## Data model
- DB state (`agent-health` namespace): `alerted` = list of currently-stale agent
  names (for dedup across hourly runs).
- Reads `runs` table via `db.get_run_history(name, limit=20)`.

## Files
- `agents/agent_health.py` — the monitor (new)
- `agents/runner.py` — registered `"agent-health"` in `AGENT_REGISTRY`
- `tests/test_agent_health.py` — unit tests for cron parsing, staleness, dedup
- crontab — added `0 * * * * run-agent.sh agent-health` (hourly)

## Limitations / future
- The monitor doesn't watch itself — if its own cron line is dropped, nothing
  alerts. A future dead-man's-switch (e.g. external heartbeat) could cover this.
- `cron_interval_seconds` covers the schedule shapes in use; exotic expressions
  (lists, ranges) raise `ValueError` rather than guessing.
