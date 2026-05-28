# Plant Sensitivity, Overwatering Warning & Underwatering Prevention

**Date:** 2026-05-26  
**Status:** Approved

## Problem

Two gaps in the plant weather system:

1. The overwatering warning uses a flat 60% threshold regardless of plant type. A cactus and a fern have radically different tolerances — cacti can die from one extra watering, ferns tolerate consistently moist soil.

2. Underwatering prevention is reactive: a task is only created the morning a plant is due. For outdoor plants facing a heatwave, the user needs to water the evening before or morning of, not after the heat has already peaked.

## Three Systems

### System A — Overwatering warning (email only)

The `overwatering_risk` list in the daily briefing email, indoor plants only.

**Why indoor only:** Outdoor plant scheduling is weather-driven by design. A shorter interval caused by heat or rain logic is appropriate, not a risk. Flagging it would create noise.

**`water_sensitivity` field:** New per-plant field, values `"high"` / `"medium"` / `"low"`. Defaults to `"medium"` when absent (backwards compatible). Auto-researched via Antigravity at `add_plant()` time.

Research-backed thresholds (from horticultural literature):
- `high` (cacti, succulents, snake plant, ZZ plant): soil must fully dry between waterings. Warn if `effective_interval < frequency_days * 0.8`
- `medium` (most houseplants — pothos, monstera, philodendron): partial drying needed. Warn if `effective_interval < frequency_days * 0.6` (existing behaviour)
- `low` (ferns, peace lily, carnivorous plants): prefer moist soil. Warn if `effective_interval < frequency_days * 0.4`

### System B — Underwatering prevention (Todoist task)

`PlantWeatherAgent` (hourly) already creates tasks when `adjusted <= today`. Two additions:

**Heatwave look-ahead:** For outdoor plants only, if a heatwave is incoming (`is_heatwave_incoming(weather)` returns True) AND `adjusted <= tomorrow`, create the task today with content `"Water [plant] before heatwave"`. This fires up to 24h earlier than the normal same-day task.

**Rain self-cancellation:** No special handling needed. `_outdoor_adjustment` already returns a positive adjustment when rain is forecast, pushing `adjusted` out and causing `adjusted <= tomorrow` to be false. The look-ahead naturally doesn't fire when rain makes watering unnecessary.

`is_heatwave_incoming(weather: dict) -> bool` is a pure helper in `plant_weather.py`: returns True when `hot_days >= 2` (≥2 forecast days with `temp_max_c > 30`) and no meaningful rain is forecast (all `precip_mm < 5`).

### System C — Heatwave timing note in briefing

When heatwave look-ahead tasks are scheduled, `_check_plants()` populates a `heatwave_timing` list for the briefing. `_build_prompt()` injects it. The email template renders a note in the Watering Advisory section: *"Water these outdoor plants this evening or tomorrow morning before temperatures peak: [list]."*

## Data Model

Plant dict gains one new field:

```python
{
    "name": str,
    "frequency_days": int,
    "last_watered": str,       # YYYY-MM-DD
    "location": str,           # "indoor" | "outdoor"
    "sunlight": str,           # "full sun" | "partial shade" | "shade"
    "water_sensitivity": str,  # "high" | "medium" | "low"  ← NEW
}
```

No schema changes to SQLite — plant data lives in the `state` table as JSON.

## Architecture

### `agents/plant_weather.py`
- Add `is_heatwave_incoming(weather: dict) -> bool` — pure function, no I/O

### `agents/plant_weather_agent.py`
- `_update_adjustments()`: for outdoor plants, if `is_heatwave_incoming(weather)` and `adjusted <= today + 1 day`, add to `tasks_to_create` with `heatwave=True` flag
- `_create_tasks()`: use `heatwave` flag to set task content to `"Water [plant] before heatwave"`

### `agents/daily_briefing.py`
- `_check_plants()`:
  - Indoor plants: use `SENSITIVITY_THRESHOLDS` dict for `overwatering_risk`
  - Outdoor plants: skip overwatering check; instead populate `heatwave_timing` when adjustment reason contains heatwave signal
  - Return `heatwave_timing` in result dict
- `_build_prompt()`: inject `heatwave_timing` section

### `agents/prompts/daily_briefing.md`
- Watering Advisory section: render heatwave timing note when `heatwave_timing` is non-empty

### `telegram-bot/tools.py`
- Add `research_plant_water_sensitivity(plant_name: str) -> str` — same pattern as `research_plant_sunlight()`
- `add_plant()`: call `research_plant_water_sensitivity()` automatically and store result

## Testing

- `tests/test_plant_weather.py`: tests for `is_heatwave_incoming()` — heatwave detected, heatwave + rain cancels, no heatwave
- `tests/test_daily_briefing.py`:
  - `TestOverwateringRisk`: update threshold test to use `water_sensitivity` field; add tests for each sensitivity level
  - `TestHeatwaveTiming`: outdoor plant with heatwave → `heatwave_timing` populated; outdoor plant with rain → not populated
- `telegram-bot/test_tools.py`: `research_plant_water_sensitivity()` returns valid value; `add_plant()` stores `water_sensitivity`

## File List

- `agents/plant_weather.py` — add `is_heatwave_incoming()`
- `agents/plant_weather_agent.py` — heatwave look-ahead in `_update_adjustments()` + `_create_tasks()`
- `agents/daily_briefing.py` — per-sensitivity thresholds, `heatwave_timing` list
- `agents/prompts/daily_briefing.md` — heatwave timing note in advisory section
- `telegram-bot/tools.py` — `research_plant_water_sensitivity()`, update `add_plant()`
- `tests/test_plant_weather.py` — `is_heatwave_incoming()` tests
- `tests/test_daily_briefing.py` — sensitivity threshold tests, heatwave timing tests
- `telegram-bot/test_tools.py` — sensitivity research + add_plant tests
