# Travel Agent

**Created:** 2026-05-17  
**Status:** Active

## Problem

Needed a way to research trips (search for flights + hotels) and plan itineraries (when flights/hotels are already booked) — both triggerable remotely from the Telegram concierge bot, with output saved to `output/`.

## Design Decisions

### Agent over Skill
A `BaseAgent` subclass was chosen over a Claude Code skill because:
- Can be triggered remotely via the MCP bridge and Telegram bot
- Saves output to `output/` with dedup
- Gets Claude→Gemini failover from `BaseAgent.synthesize()`
- Skills are interactive terminal commands; this is a background task

### No External API Keys
Flight and hotel data is sourced via Claude's `WebSearch` during `synthesize()`. This avoids API key management and rate limits for low-frequency personal use. Amadeus/Skyscanner APIs could be added later if real-time pricing becomes important.

### Two Modes (single agent)
Rather than two separate agents or a skill + agent, a `--mode` flag on one agent covers both use cases cleanly:
- `search` — Claude researches flights, hotels, activities
- `plan` — user provides existing bookings; Claude builds day-by-day itinerary

### Weather via Open-Meteo Geocoding
The agent geocodes the destination via `geocoding-api.open-meteo.com` (free, no API key) then calls the existing `fetch_weather()` with custom coordinates. Weather failure is non-fatal — `fetch_weather` returns `None` and the prompt notes "unavailable".

## Architecture

```
TravelAgent(BaseAgent)
  configure(args)              # accepts CLI args before run()
  steps()                      # dynamic: search or plan mode
    fetch_weather              # Open-Meteo geocode + forecast
    research_travel            # synthesize() → search prompt (side_effects=True)
    plan_trip                  # synthesize() → plan prompt (side_effects=True)
    save_report                # write HTML to output/
  report()                     # return output path
```

## Files

| File | Purpose |
|------|---------|
| `agents/travel_agent.py` | `TravelAgent(BaseAgent)` |
| `agents/prompts/travel_agent_search.md` | Synthesis prompt: search mode |
| `agents/prompts/travel_agent_plan.md` | Synthesis prompt: plan mode |
| `agents/runner.py` | Registry + travel-specific CLI args |

## Usage

**Search mode** — find flights + hotels:
```bash
python3 -m agents travel-agent \
  --destination "Barcelona" \
  --origin "Dublin" \
  --checkin 2026-07-01 \
  --checkout 2026-07-07
```

**Plan mode** — itinerary from existing bookings:
```bash
python3 -m agents travel-agent \
  --mode plan \
  --destination "Barcelona" \
  --checkin 2026-07-01 \
  --checkout 2026-07-07 \
  --flights "Ryanair FR1234 DUB->BCN 06:30, return FR1235 BCN->DUB 22:00" \
  --hotel "H10 Marina Barcelona, check-in 1 Jul, 6 nights"
```

Output is saved to `output/travel-<dest>-<checkin>.html`.

## Extending

- **Real flight data:** Add Amadeus SDK call in `_fetch_flights()` step before `research_travel`; inject structured data into the search prompt.
- **Telegram bot trigger:** Add a `get_travel_status` tool in `telegram-bot/tools.py` that reads the latest `output/travel-*.html` file and returns a summary.
- **Multi-destination trips:** Accept multiple `--destination` values and loop steps per leg.
