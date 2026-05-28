# Master Plant AI Agent — Design Doc

**Date:** 2026-05-28  
**Status:** Approved

## Problem

Plant intelligence is currently scattered across three files:
- `agents/plant_weather_agent.py` — hourly weather cache + task creation
- `agents/daily_briefing.py` — watering sync + check_plants logic
- `telegram-bot/bot.py` — photo assessment

This makes the system hard to reason about, improve, or train. Knowledge about individual plants is ephemeral — no structured memory accumulates across runs.

## Solution

A single `agents/plant_agent.py` that:
- Owns all plant scheduling, intelligence, and email output
- Accumulates structured per-plant knowledge in `docs/plants/<name>.md` profile docs
- Runs hourly via cron but gates expensive steps internally
- Concentrates LLM spend on knowledge-building (photo assessment, intelligence runs) and minimises spend on routine operations

## Architecture

### Frequency-Gated Steps

The agent runs hourly. Each step checks a `last_<step>` timestamp in SQLite state before executing.

| Step | Frequency | LLM | Notes |
|---|---|---|---|
| `weather_update` | Every run | No | Pure Python HTTP to Open-Meteo |
| `sync_watering` | Daily | Yes, tight | Fetch Todoist completed "Water *" tasks, update last_watered |
| `create_tasks` | 2×/day | Yes, minimal | Python short-circuits if nothing due |
| `photo_requests` | Daily | No | Python reads profiles + flags, sends Telegram HTTP POST |
| `send_status_email` | Daily | Yes, short | Pre-computed data, LLM formats + sends only |
| `intelligence_run` | Every 3 days | Yes, full | Full history, full profile docs, no context caps |

**Estimated: ~4–5 scheduled LLM calls/day** (down from ~26 today)

### Frequency Gating Pattern

```python
def _gate(self, key: str, hours: int) -> bool:
    last = self.get_state(f"last_{key}")
    if not last:
        return True
    return (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > hours * 3600

def _mark_ran(self, key: str):
    self.set_state(f"last_{key}", datetime.now(timezone.utc).isoformat())
```

### Per-Plant Profile Docs

Each plant gets `docs/plants/<name>.md` — a structured markdown file accumulating observations, health assessments, watering history, and frequency adjustments. Created lazily on first intelligence run.

Format:
```markdown
# <Plant Name>

## Plant Info
- Location: <indoor|outdoor> | Sunlight: <value>
- Water sensitivity: <high|medium|low> | Base frequency: <N> days

## Observed Behaviour
<!-- Updated by intelligence run -->

## Health Assessments
### YYYY-MM-DD — <Status>
<structured notes from photo assessment>

## Frequency History
| Date | Change | Reason |
|---|---|---|

## Intelligence Notes
<!-- Appended by each intelligence run -->
```

### Photo Assessment (Rich, Dedicated)

When a photo arrives in Telegram:
1. Load plant profile doc if it exists
2. Single rich LLM call via vision model — outputs structured JSON
3. Bot appends structured notes to `docs/plants/<name>.md`
4. If frequency change suggested → Telegram inline keyboard (Apply ✓ / Dismiss ✗)

Assessment JSON format:
```json
{
  "status": "Healthy|Stressed|Overwatered|Underwatered|Concerning",
  "summary": "2-3 sentence assessment",
  "observations": ["observation 1", "..."],
  "watering_recommendation": "immediate|on_schedule|delay",
  "frequency_suggestion": null,
  "profile_notes": "### YYYY-MM-DD — Healthy\n..."
}
```

### Intelligence Run (Every 3 Days, Full Context)

Reads: all plant profile docs + full agent-notes.md + current plant state JSON + 14-day weather forecast

Outputs (delimited sections for Python parsing):
- `[PROFILE:name]...[/PROFILE]` — updates to each plant profile doc
- `[EMAIL]...[/EMAIL]` — insight email body
- `[NEEDS_PHOTO]plant1,plant2[/NEEDS_PHOTO]` — plants flagged for photo check
- `[TASKS]...[/TASKS]` — Todoist action task instructions

### Photo Requests (Daily, No LLM)

Python checks:
- `needs_photo` flag set by intelligence run
- Last assessment date > 14 days ago
- Overwatering/underwatering risk flag

Sends targeted Telegram message directly via `requests.post("https://api.telegram.org/bot{TOKEN}/sendMessage", ...)`.
Rate limit: max 1 request per plant per 3 days (tracked in state).

### Daily Briefing Changes

Remove `_sync_plant_completions()`, `_check_plants()`, `_fetch_weather()` and all plant prompt sections from `agents/daily_briefing.py`. Plant agent owns all plant output.

## Data Model

No schema changes. Plant profiles stored as `docs/plants/<name>.md` files. Frequency gating tracked via `get_state`/`set_state` (key pattern: `last_<step>`). Photo request rate limiting tracked in a state dict `photo_request_timestamps`.

## Files

| Action | File |
|---|---|
| NEW | `agents/plant_agent.py` |
| NEW | `agents/prompts/plant_photo_assessment.md` |
| NEW | `agents/prompts/plant_intelligence.md` |
| NEW | `agents/prompts/plant_status_email.md` |
| NEW | `docs/plants/<name>.md` (runtime, one per plant) |
| DELETE | `agents/plant_weather_agent.py` |
| MODIFY | `agents/daily_briefing.py` |
| MODIFY | `agents/prompts/daily_briefing.md` |
| MODIFY | `telegram-bot/bot.py` |
| MODIFY | `telegram-bot/tools.py` |
