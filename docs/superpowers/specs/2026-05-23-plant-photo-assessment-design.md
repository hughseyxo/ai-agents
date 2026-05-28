# Plant Photo Health Assessment — Design Spec

## Problem

The concierge Telegram bot can report plant watering schedules and record waterings, but has no way to assess plant health visually. The user wants to send a photo of a plant and receive an AI health assessment with actionable advice, informed by that plant's history.

## Solution

Extend the concierge bot to handle photo messages. When a photo is sent with a caption naming the plant, the bot fetches the plant's history, passes both to a vision LLM, stores the assessment summary on the plant record, and replies with the analysis.

## Architecture

### Data model change

The plant dict in SQLite (`daily-briefing` → `plants`) gains one optional field:

```python
{
    "name": "Monstera",
    "frequency_days": 7,
    "last_watered": "2026-05-23",
    "location": "indoor",
    "last_assessment": {        # optional — absent on old records
        "date": "2026-05-23",
        "summary": "Leaves healthy but slightly pale..."
    }
}
```

No migration required — `plant.get("last_assessment")` returns `None` on old records.

### Data flow

1. User sends photo with caption naming the plant (e.g. `"monstera"`)
2. `handle_photo` downloads the highest-resolution photo as bytes
3. Resolves plant: exact name match first, then substring, against `AgentDB`. If not found → reply with error listing known plants
4. Calls `_analyze_plant_image(image_bytes, plant)`:
   - Builds prompt including: plant name, location, last watered date, days since watering
   - Tries `VISION_MODELS` (free OpenRouter vision models) in order
   - If all fail → saves image to tempfile, calls `antigravity -y -p <prompt> -o text <path>`
   - If Antigravity also fails → returns error string
5. `handle_photo` calls `save_plant_assessment(plant_name, summary)` to persist `last_assessment` to DB
6. Replies to user with full assessment text

### Components

**`telegram-bot/bot.py`**

- `VISION_MODELS: list[str]` — ordered list of free OpenRouter vision models:
  - `"meta-llama/llama-3.2-11b-vision-instruct:free"`
  - `"qwen/qwen2.5-vl-7b-instruct:free"`
- `_analyze_plant_image(image_bytes: bytes, plant: dict) -> str` — pure function, testable. Encodes image as base64 data URL, builds contextual prompt, tries vision models, falls back to Antigravity CLI tempfile
- `handle_photo(update, context)` — async handler: auth gate, download photo, resolve plant, call helper, save assessment, reply
- Registration: `app.add_handler(MessageHandler(filters.PHOTO, handle_photo))`

**`telegram-bot/tools.py`**

- `save_plant_assessment(plant_name: str, summary: str) -> str` — looks up plant by exact/substring match, writes `last_assessment: {date, summary}`, saves back via `db.set_state`

### Vision prompt

```
You are a plant health expert. This is a photo of {name}'s plant, located {location}.
It was last watered {last_watered} ({days_since} days ago).

Assess the plant's health: look for signs of overwatering or underwatering, pests,
disease, nutrient deficiencies, leaf discolouration, or drooping.
Give specific observations and concise actionable advice (3–5 sentences).
```

### Error handling

| Scenario | Response |
|---|---|
| Caption names unknown plant | "No plant named X found. Known plants: A, B, C" |
| No caption | "Please include the plant name as a caption (e.g. 'monstera')" |
| All vision models + Antigravity fail | "Plant assessment unavailable right now. Try again later." |
| DB write fails | Assessment still returned to user; log the DB error |

## Testing

**`telegram-bot/test_bot.py`** — unit tests for `_analyze_plant_image`:
- Vision model succeeds on first try → returns assessment
- First model fails, second succeeds → returns assessment
- All OpenRouter models fail → Antigravity CLI called with tempfile
- Antigravity also fails → graceful error string returned

**`telegram-bot/test_tools.py`** — unit tests for `save_plant_assessment`:
- Exact name match → saves `last_assessment` with today's date
- Substring match → saves correctly
- Unknown plant → returns error string
- DB error → returns error string

## Files changed

- `telegram-bot/bot.py` — add `VISION_MODELS`, `_analyze_plant_image`, `handle_photo`, register handler
- `telegram-bot/tools.py` — add `save_plant_assessment`
- `telegram-bot/test_bot.py` — add vision tests
- `telegram-bot/test_tools.py` — add `save_plant_assessment` tests
