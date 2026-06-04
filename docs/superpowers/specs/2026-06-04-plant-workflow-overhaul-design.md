# Plant Workflow Overhaul — Design Doc

**Date:** 2026-06-04  
**Status:** Implemented

## Problem

Four categories of weakness in the plant agent workflow:

1. **Silent failures** — intelligence output parsed via brittle regex markers (`[PROFILE:]`, `[FREQUENCY]`, `[NEEDS_PHOTO]`, `[PRUNING]`) with no validation; failures silently dropped; photo assessments never reached plant profile docs.
2. **Data model drift** — plants as raw dicts accessed ad-hoc across CLI, bot, and agent; `baseline_frequency_days` missing from CLI-added plants; no shared serialization layer.
3. **Weather logic bugs** — dead code branch (`elif recent_rain > 10: rain_adj = 2`) was unreachable; weather fetched up to 3× per agent run.
4. **Missing integration** — `save_plant_assessment()` existed in tools.py but was not registered in tool_specs.py; `## Health Assessments` section in plant profiles never populated.

## Design Decisions

### Pydantic models (plant_model.py)

Single source of truth for all plant data:

- `Plant` — typed model with field validators (frequency clamped 1–30, invalid location/sensitivity coerced to defaults on migration)
- `AssessmentRecord` — date + summary + status for last photo assessment
- `PlantStore` — wraps AgentDB; owns typed read/write; migrates legacy dicts on first read (missing `baseline_frequency_days` defaults to `frequency_days`)
- `PlantIntelligenceResult` + entry models — parse and validate intelligence output JSON

### JSON intelligence output

Replaced text markers with a structured JSON block in Step 4 of the intelligence prompt:

```json
{
  "plants": [{"name": "...", "status": "Healthy|Stressed|...", "notes": [], "needs_photo": false, "frequency_change": null}],
  "pruning": [{"name": "...", "action": "...", "reason": "..."}],
  "tasks_created": [],
  "email_sent": true
}
```

`PlantIntelligenceResult.from_llm_output()` strips code fences and validates via Pydantic. On parse failure: logs raw output, returns without marking the gate — next hourly run retries automatically.

### Photo → profile integration

`write_health_assessment(plant_name, profile_notes)` in plant_profiles.py appends to `## Health Assessments` section of the plant's profile doc. Creates the section if missing. Called from bot.py after `assess_image()` succeeds.

### Weather caching

`_weather_update()` stores result in `self.context["weather"]`. `_create_tasks()` reads from context instead of re-fetching. Reduces fetch calls from 3 to 1 per agent run.

### Dead code removal

Removed `elif recent_rain > 10: rain_adj = 2` from `_outdoor_adjustment()` — unreachable because `total_rain >= recent_rain`, so `recent_rain > 10` implies `total_rain > 10` is already handled.

### save_plant_assessment registration

Added the existing `save_plant_assessment` tool to the SPECS list in tool_specs.py, making it accessible via the concierge MCP server.

### CLI baseline fix

`add_plant()` in tools.py now writes both `frequency_days` and `baseline_frequency_days` to prevent hourly weather recompute from overwriting user-set frequency.

## Architecture

```
PlantStore (plant_model.py)
  ├── get_plants() → list[Plant]   # migrates legacy dicts
  ├── save_plants(plants)
  ├── get_plant(name) → Plant|None  # case-insensitive + substring
  └── update_plant(plant) → bool

PlantIntelligenceResult (plant_model.py)
  └── from_llm_output(str) → PlantIntelligenceResult  # strips fences, validates JSON

write_health_assessment (plant_profiles.py)
  └── appends to ## Health Assessments section in docs/plants/<slug>.md

PlantAgent._apply_intelligence_output()
  └── validates JSON → applies notes, frequency changes, needs_photo flags, pruning
```

## Files Modified

| File | Change |
|---|---|
| `agents/plant_model.py` | NEW — Plant, AssessmentRecord, PlantStore, PlantIntelligenceResult |
| `agents/plant_agent.py` | PlantStore usage; weather context pass-through; JSON-based output parsing |
| `agents/plant_weather.py` | Remove dead `recent_rain > 10` branch |
| `agents/plant_profiles.py` | Add `write_health_assessment()` |
| `agents/prompts/plant_intelligence.md` | Replace text markers with JSON schema |
| `telegram-bot/tools.py` | Write `baseline_frequency_days` on add_plant |
| `telegram-bot/tool_specs.py` | Register `save_plant_assessment` in SPECS |
| `telegram-bot/bot.py` | Call `write_health_assessment()` after photo assessment |
| `tests/test_plant_model.py` | NEW — Plant, PlantStore, PlantIntelligenceResult coverage |
| `tests/test_plant_profiles.py` | Extended — `write_health_assessment` tests |
| `tests/test_plant_agent.py` | Updated — intelligence tests use JSON format |
| `tests/test_plant_weather.py` | Extended — dead branch removal verified |
