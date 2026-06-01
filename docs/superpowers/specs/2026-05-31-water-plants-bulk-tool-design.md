# Design: Bulk Plant Watering Tool

**Date:** 2026-05-31  
**Status:** Approved

## Problem

The Telegram concierge bot supports watering a single plant via `water_plant(plant_name)`, but has no way to mark all plants in a location (e.g. all outdoor plants) as watered in one message. The user wants to say "watered all outdoor plants" and have the bot update all matching records.

## Design Decision

Add a `water_plants(location)` function to `tools.py` and register it as an LLM tool in `bot.py`. The function filters the plant list by location and bulk-updates `last_watered` to today for all matches. No Todoist interaction — DB only, consistent with `water_plant`.

## Architecture

### `tools.py` — new function

```python
def water_plants(location: str) -> str:
```

- Opens `AgentDB`, reads plant list from state `("daily-briefing", "plants")`
- Filters by `p["location"] == location`
- Sets `last_watered = date.today().isoformat()` on each match
- Writes updated list back with `set_state`
- Returns: `"Marked N {location} plants as watered today: Name1, Name2, ..."` 
- Returns `"No {location} plants found."` if the filter yields nothing

### `bot.py` — tool registration

Add to `TOOL_FUNCTIONS`:
```python
"water_plants": water_plants,
```

Add to import from `tools`.

Add to `TOOLS` list:
```python
{
    "type": "function",
    "function": {
        "name": "water_plants",
        "description": "Record that all plants in a given location were watered today. Use when the user says they watered all indoor or all outdoor plants.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "enum": ["indoor", "outdoor"],
                    "description": "Location of the plants to mark as watered ('indoor' or 'outdoor').",
                }
            },
            "required": ["location"],
        },
    },
},
```

## Data Flow

1. User sends: "watered all outdoor plants"
2. LLM receives message + TOOLS list
3. LLM calls `water_plants(location="outdoor")`
4. `water_plants` updates all outdoor plants in SQLite, returns summary string
5. LLM relays summary to user

## Error Handling

- No plants of that location: returns informative string, no error raised
- DB failure: caught by `except Exception as e`, returns `"Failed to update plants: {e}"`

## Testing

New cases in `tests/test_tools.py`:

| Case | Expected |
|------|----------|
| 2 outdoor + 1 indoor, call `water_plants("outdoor")` | Both outdoor updated, indoor untouched, summary names both |
| 2 indoor, call `water_plants("outdoor")` | Returns "No outdoor plants found." |
| Call `water_plants("indoor")` | Updates only indoor plants |
| Empty plant list | Returns "No indoor/outdoor plants found." |

## Files Changed

- `telegram-bot/tools.py` — add `water_plants()`
- `telegram-bot/bot.py` — import + register tool
- `tests/test_tools.py` — add test cases
