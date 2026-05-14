# Weather-Aware Plant Watering

**Date:** 2026-05-14  
**Status:** Implemented

## Problem
Plant watering schedules were fixed intervals (`frequency_days`). A heatwave or rainy week had no effect — plants got watered on the same rigid schedule regardless.

## Design decisions
- **Weather API:** Open-Meteo (free, no API key, Leiden: 52.16°N, 4.49°E)
- **Weather fetch in Python**, not LLM — deterministic, testable, works identically for both Claude and Gemini CLI
- **Adjustments are automatic** — shift the actual watering date and Todoist task, not just advisory

## Architecture

```
Daily Briefing Agent steps:
  1. weather  → fetch_weather() → self.context["weather"]
  2. plants   → _check_plants() reads weather, applies adjustments
  3. briefing → LLM CLI synthesizes email with adjusted dates + reasons
```

Weather fetching (`agents/weather.py`) and adjustment logic (`agents/plant_weather.py`) are pure Python — no LLM, no MCP, no side effects. The LLM only formats the final output.

## Data model

```json
{
  "name": "Monstera Deliciosa",
  "frequency_days": 10,
  "last_watered": "2026-05-17",
  "location": "indoor"
}
```

`location` is `"indoor"` (default) or `"outdoor"`. Legacy plants without the field are treated as indoor.

## Adjustment rules

### Indoor (±1-2 days)
| Condition | Adjustment |
|-----------|-----------|
| temp >32°C AND humidity <35% | -2 days |
| temp >28°C AND humidity <40% | -1 day |
| temp <5°C AND humidity >85% | +2 days |
| temp <10°C AND humidity >80% | +1 day |
| Otherwise | 0 |

### Outdoor (±1-3 days)
| Condition | Adjustment |
|-----------|-----------|
| Total rain (recent + forecast day 1) >10mm | +3 days |
| Recent rain >5mm | +2 days |
| Forecast rain day 1 >5mm | +1 day |
| Heatwave: 2+ forecast days >30°C | -2 days |
| Dry spell: no forecast rain + temp >25°C | -1 day |

Rain overrides heat — if it's hot but raining, watering is deferred, not advanced.

All adjustments clamped to ±3 days max. Effective interval never reduced below 2 days.

## Weather API

Open-Meteo, no authentication:
- Current: temperature, humidity, precipitation
- Hourly: precipitation (for 24h sum)
- Daily: 3-day forecast (max temp, precipitation sum)
- Returns `None` on any failure — caller skips adjustments gracefully

## CLI

```bash
plant.sh add "Tomato" 3 --outdoor   # outdoor plant, water every 3 days
plant.sh add "Fern" 5               # indoor (default), every 5 days
plant.sh list                       # shows PLANT, FREQ, LOCATION, LAST WATERED, NEXT WATER
plant.sh remove "Tomato"
```

## Files
- `agents/weather.py` — Open-Meteo client
- `agents/plant_weather.py` — adjustment logic (pure functions)
- `agents/daily_briefing.py` — weather step + updated `_check_plants()`
- `agents/prompts/daily_briefing.md` — adjustment reasons in briefing output
- `plant.sh` — `--outdoor` flag, location column
- `tests/test_weather.py` — 8 tests
- `tests/test_plant_weather.py` — 24 tests
- `tests/test_daily_briefing.py` — 8 tests
