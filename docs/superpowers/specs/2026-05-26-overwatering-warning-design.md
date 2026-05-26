# Overwatering Warning & Weather-Responsive Task Creation

**Date:** 2026-05-26  
**Status:** Approved

## Problem

Two related gaps in the plant weather system:

1. `MIN_INTERVAL_DAYS = 2` silently overrides valid heat adjustments. When outdoor plants need watering sooner due to a heatwave, the floor blocks the adjusted date from reaching today — so no Todoist task is created and the user gets no notification. They have to manually notice and water plants without any system prompt.

2. The daily briefing email has no signal when weather conditions are pushing plants to unusually short watering intervals. The user has no visibility into "the system is scheduling outdoor plants more aggressively than normal right now."

## Design Decisions

### Remove `MIN_INTERVAL_DAYS`

The constant in `agents/plant_weather.py` was meant to prevent absurd recommendations, but in practice it suppresses legitimate heat-driven adjustments. The correct guard is the `PlantWeatherAgent` dedup key (`{name}:{today}`) which already prevents multiple task creations per day. Remove the floor entirely and let weather drive dates freely.

Edge case: plants with `frequency_days ≤ 2` and a full `-2d` adjustment could compute a due date at or before `last_watered`. This is an obscure case (no plant in the tracker has a 2-day cycle), and the dedup key prevents duplicate tasks if it does occur.

### Hourly task creation stays as-is

`PlantWeatherAgent._create_tasks` already creates a Todoist task when `adjusted <= today`. With the floor removed, heat adjustments will properly land on today and the task will fire. No structural change to `_create_tasks` is needed.

### Overwatering warning in daily briefing (Option A — dedicated section)

Add a "🌡 Watering Advisory" section to the briefing email when any plant's effective watering interval (adjusted_date − last_watered in days) is less than 60% of its normal `frequency_days`. This threshold catches genuine schedule compression without flagging minor 1-day adjustments on long-cycle plants.

Threshold: `effective_interval < frequency_days * 0.6`

The warning is informational — it doesn't block task creation or change the schedule. It tells the user "these plants are being scheduled more often than usual; check conditions before watering."

## Architecture

### `agents/plant_weather.py`
- Delete the `MIN_INTERVAL_DAYS = 2` constant and the guard block in `adjust_watering_date()` that uses it

### `agents/daily_briefing.py`
- `_check_plants()`: for each plant, compute `effective_interval = (adjusted - last_watered).days`. If `effective_interval < frequency_days * 0.6`, append to a new `overwatering_risk` list in the return dict.
- `_build_prompt()`: inject `overwatering_risk` into the prompt text as a new section.

### `agents/prompts/daily_briefing.md`
- Add a "🌡 Watering Advisory" HTML block to the email template, rendered only when `overwatering_risk` is non-empty. Orange left-border, warning tone. Lists each at-risk plant with its effective interval vs normal cycle.

### Tests
- `tests/test_plant_weather.py`: remove assertions that relied on MIN_INTERVAL_DAYS clamping; add test confirming a `-3d` adjustment is now applied without flooring.
- `tests/test_synthesize.py` or a new test: verify `_check_plants()` populates `overwatering_risk` correctly at the 60% threshold boundary.

## Data Model

No schema changes. `plant_weather_cache` and the `state` table are unchanged.

## File List

- `agents/plant_weather.py` — remove MIN_INTERVAL_DAYS
- `agents/daily_briefing.py` — add overwatering_risk detection + prompt injection
- `agents/prompts/daily_briefing.md` — add Watering Advisory HTML section
- `tests/test_plant_weather.py` — update tests
- `tests/test_daily_briefing.py` or new test file — add threshold tests (if briefing tests exist)
