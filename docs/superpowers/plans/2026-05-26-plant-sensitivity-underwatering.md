# Plant Sensitivity & Underwatering Prevention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-plant water sensitivity thresholds to the overwatering warning, add a heatwave look-ahead that creates Todoist tasks one day early, and emit a heatwave timing note in the daily briefing email.

**Architecture:** Three changes build on each other — `is_heatwave_incoming()` is a pure helper added to `plant_weather.py`; daily_briefing.py uses it plus new `SENSITIVITY_THRESHOLDS` dict to produce richer plant data; plant_weather_agent.py uses it to create look-ahead tasks. Telegram tools auto-research `water_sensitivity` at `add_plant()` time. All tests are TDD-first.

**Tech Stack:** Python 3.11+, pytest, unittest.mock; no new dependencies.

---

## File Structure

| File | Change |
|------|--------|
| `agents/plant_weather.py` | Add `is_heatwave_incoming(weather: dict) -> bool` pure helper |
| `agents/daily_briefing.py` | Add `SENSITIVITY_THRESHOLDS`; update `_check_plants()` to use sensitivity thresholds (indoor only) and populate `heatwave_timing` (outdoor); update `_build_prompt()` to inject heatwave section; update import |
| `agents/plant_weather_agent.py` | Update `_update_adjustments()` with heatwave look-ahead; update `_CREATE_TASK_PROMPT` to support `{content}` variable; update `_create_tasks()` to set heatwave task content |
| `agents/prompts/daily_briefing.md` | Update Step 6 instructions + email template to render heatwave timing note |
| `telegram-bot/tools.py` | Add `SENSITIVITY_VALUES` constant; add `research_plant_water_sensitivity()`; update `add_plant()` to auto-research and store `water_sensitivity` |
| `tests/test_plant_weather.py` | Add `TestIsHeatwaveIncoming` class (3 tests) |
| `tests/test_daily_briefing.py` | Add sensitivity tests to `TestOverwateringRisk`; add `TestHeatwaveTiming` class |
| `telegram-bot/test_tools.py` | Add `research_plant_water_sensitivity()` tests; add `add_plant()` sensitivity test |

---

### Task 1: Add `is_heatwave_incoming()` to `agents/plant_weather.py`

**Files:**
- Modify: `agents/plant_weather.py`
- Test: `tests/test_plant_weather.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_plant_weather.py`, add this import at the top and this class at the bottom:

```python
from agents.plant_weather import calculate_adjustment, adjust_watering_date, is_heatwave_incoming
```

```python
# ===================================================================
# is_heatwave_incoming — pure helper
# ===================================================================

class TestIsHeatwaveIncoming:
    def test_heatwave_detected(self):
        """≥2 forecast days >30°C with no rain → True."""
        weather = _weather(forecast=[
            {"date": "2026-05-26", "temp_max_c": 33, "precip_mm": 0},
            {"date": "2026-05-27", "temp_max_c": 34, "precip_mm": 0},
            {"date": "2026-05-28", "temp_max_c": 31, "precip_mm": 0},
        ])
        assert is_heatwave_incoming(weather) is True

    def test_heatwave_with_rain_returns_false(self):
        """Hot days but ≥5mm rain forecast on any day → False."""
        weather = _weather(forecast=[
            {"date": "2026-05-26", "temp_max_c": 33, "precip_mm": 8},
            {"date": "2026-05-27", "temp_max_c": 34, "precip_mm": 0},
            {"date": "2026-05-28", "temp_max_c": 31, "precip_mm": 0},
        ])
        assert is_heatwave_incoming(weather) is False

    def test_only_one_hot_day_returns_false(self):
        """Only 1 day >30°C → not a heatwave."""
        weather = _weather(forecast=[
            {"date": "2026-05-26", "temp_max_c": 33, "precip_mm": 0},
            {"date": "2026-05-27", "temp_max_c": 25, "precip_mm": 0},
            {"date": "2026-05-28", "temp_max_c": 22, "precip_mm": 0},
        ])
        assert is_heatwave_incoming(weather) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/cian/git/ai-agents
pytest tests/test_plant_weather.py::TestIsHeatwaveIncoming -v
```

Expected: `ImportError` or `AttributeError` — `is_heatwave_incoming` not defined yet.

- [ ] **Step 3: Implement `is_heatwave_incoming()` in `agents/plant_weather.py`**

Add this function after `_build_reason()` (end of file):

```python
def is_heatwave_incoming(weather: dict) -> bool:
    """Return True when ≥2 forecast days >30°C and no forecast day has ≥5mm rain."""
    forecast = weather.get("forecast", [])
    hot_days = sum(1 for f in forecast if f["temp_max_c"] > 30)
    all_dry = all(f["precip_mm"] < 5 for f in forecast)
    return hot_days >= 2 and all_dry
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_plant_weather.py::TestIsHeatwaveIncoming -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest tests/test_plant_weather.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add agents/plant_weather.py tests/test_plant_weather.py
git commit -m "feat: add is_heatwave_incoming() helper to plant_weather"
```

---

### Task 2: Per-sensitivity overwatering thresholds in `agents/daily_briefing.py`

The existing flat 60% threshold becomes the "medium" default. Indoor-only check. Outdoor plants are never flagged — weather-driven shorter cycles are by design.

**Files:**
- Modify: `agents/daily_briefing.py`
- Test: `tests/test_daily_briefing.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `TestOverwateringRisk` in `tests/test_daily_briefing.py`. The existing 4 tests remain — this adds 4 more:

```python
    def test_high_sensitivity_flagged_at_80_percent(self):
        """water_sensitivity='high' uses 0.8 threshold — fires more readily.

        freq=5, indoor, hot_dry (adj=-2) → effective=3
        threshold = 5 * 0.8 = 4.0 → 3 < 4.0 → FLAGGED
        """
        today = datetime.now(timezone.utc).date()
        last_watered = today.isoformat()
        plants = [{"name": "Cactus", "frequency_days": 5,
                   "last_watered": last_watered, "location": "indoor",
                   "water_sensitivity": "high"}]

        agent = _make_agent_with_plants(plants, HOT_DRY_WEATHER)
        result = agent._check_plants()

        assert len(result["overwatering_risk"]) == 1
        assert result["overwatering_risk"][0]["name"] == "Cactus"

    def test_low_sensitivity_not_flagged_at_40_percent(self):
        """water_sensitivity='low' uses 0.4 threshold — ferns tolerate short intervals.

        freq=5, indoor, hot_dry (adj=-2) → effective=3
        threshold = 5 * 0.4 = 2.0 → 3 < 2.0? NO → NOT flagged
        """
        today = datetime.now(timezone.utc).date()
        last_watered = today.isoformat()
        plants = [{"name": "Fern", "frequency_days": 5,
                   "last_watered": last_watered, "location": "indoor",
                   "water_sensitivity": "low"}]

        agent = _make_agent_with_plants(plants, HOT_DRY_WEATHER)
        result = agent._check_plants()

        assert result["overwatering_risk"] == []

    def test_missing_sensitivity_defaults_to_medium(self):
        """Plants without water_sensitivity field use medium threshold (0.6).

        freq=4, indoor, hot_dry (adj=-2) → effective=2
        threshold = 4 * 0.6 = 2.4 → 2 < 2.4 → FLAGGED (same as existing test_short_interval)
        """
        today = datetime.now(timezone.utc).date()
        last_watered = today.isoformat()
        plants = [{"name": "Pothos", "frequency_days": 4,
                   "last_watered": last_watered, "location": "indoor"}]  # no water_sensitivity

        agent = _make_agent_with_plants(plants, HOT_DRY_WEATHER)
        result = agent._check_plants()

        assert len(result["overwatering_risk"]) == 1

    def test_outdoor_plant_never_flagged_for_overwatering(self):
        """Outdoor plants are skipped for overwatering_risk — weather-driven is by design.

        Even high sensitivity outdoor cactus in hot dry weather is NOT flagged.
        """
        today = datetime.now(timezone.utc).date()
        last_watered = today.isoformat()
        plants = [{"name": "Succulent", "frequency_days": 5,
                   "last_watered": last_watered, "location": "outdoor",
                   "water_sensitivity": "high"}]

        agent = _make_agent_with_plants(plants, HOT_DRY_WEATHER)
        result = agent._check_plants()

        assert result["overwatering_risk"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/cian/git/ai-agents
pytest tests/test_daily_briefing.py::TestOverwateringRisk -v
```

Expected: the 4 new tests FAIL (sensitivity not yet implemented, outdoor still flagged).

- [ ] **Step 3: Update `agents/daily_briefing.py`**

Add after the `PERSONAL_PROJECT_ID` constant (around line 17):

```python
SENSITIVITY_THRESHOLDS = {
    "high": 0.8,
    "medium": 0.6,
    "low": 0.4,
}
```

Replace the overwatering block inside `_check_plants()` (the current lines 143–149):

```python
        # Current code to REMOVE:
        effective_interval = (next_water - last_watered).days
        if weather and effective_interval < plant["frequency_days"] * 0.6:
            overwatering_risk.append({
                "name": plant["name"],
                "effective_interval": effective_interval,
                "normal_frequency": plant["frequency_days"],
            })
```

Replace with:

```python
        effective_interval = (next_water - last_watered).days
        location = plant.get("location", "indoor")
        if location == "indoor" and weather:
            sensitivity = plant.get("water_sensitivity", "medium")
            threshold = SENSITIVITY_THRESHOLDS.get(sensitivity, 0.6)
            if effective_interval < plant["frequency_days"] * threshold:
                overwatering_risk.append({
                    "name": plant["name"],
                    "effective_interval": effective_interval,
                    "normal_frequency": plant["frequency_days"],
                })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_daily_briefing.py::TestOverwateringRisk -v
```

Expected: all 8 tests PASS (4 original + 4 new).

- [ ] **Step 5: Commit**

```bash
git add agents/daily_briefing.py tests/test_daily_briefing.py
git commit -m "feat: use per-plant water_sensitivity thresholds for overwatering warning"
```

---

### Task 3: Add `heatwave_timing` to daily briefing

Outdoor plants approaching a heatwave get a timing note in the email ("water this evening or tomorrow morning").

**Files:**
- Modify: `agents/daily_briefing.py`
- Modify: `agents/prompts/daily_briefing.md`
- Test: `tests/test_daily_briefing.py`

- [ ] **Step 1: Write the failing tests**

Add `TestHeatwaveTiming` class to `tests/test_daily_briefing.py`:

```python
HEATWAVE_WEATHER = {
    "current": {"temp_c": 32, "humidity_pct": 30, "precip_mm": 0},
    "forecast": [
        {"date": "2026-05-26", "temp_max_c": 33, "precip_mm": 0},
        {"date": "2026-05-27", "temp_max_c": 34, "precip_mm": 0},
        {"date": "2026-05-28", "temp_max_c": 32, "precip_mm": 0},
    ],
    "recent_precip_mm": 0,
}

RAINY_HEATWAVE_WEATHER = {
    "current": {"temp_c": 32, "humidity_pct": 50, "precip_mm": 0},
    "forecast": [
        {"date": "2026-05-26", "temp_max_c": 33, "precip_mm": 8},
        {"date": "2026-05-27", "temp_max_c": 34, "precip_mm": 0},
        {"date": "2026-05-28", "temp_max_c": 32, "precip_mm": 0},
    ],
    "recent_precip_mm": 0,
}


class TestHeatwaveTiming:
    def test_outdoor_plant_due_tomorrow_in_heatwave_added(self):
        """Outdoor plant whose adjusted date is tomorrow during heatwave → heatwave_timing populated.

        freq=5, last_watered=today-2d → base=today+3.
        HEATWAVE_WEATHER outdoor adj=-2 (2 hot days, no rain) → adjusted=today+1.
        days_until=1, is_heatwave_incoming=True → heatwave_timing=['Tomato']
        """
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=2)).isoformat()
        plants = [{"name": "Tomato", "frequency_days": 5,
                   "last_watered": last_watered, "location": "outdoor"}]

        agent = _make_agent_with_plants(plants, HEATWAVE_WEATHER)
        result = agent._check_plants()

        assert "heatwave_timing" in result
        assert "Tomato" in result["heatwave_timing"]

    def test_outdoor_plant_with_rain_not_in_heatwave_timing(self):
        """Rain in forecast: is_heatwave_incoming returns False → not in heatwave_timing.

        RAINY_HEATWAVE_WEATHER has 8mm on day 1 → is_heatwave_incoming=False.
        Also outdoor rain adj pushes adjusted later → days_until > 1.
        """
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=2)).isoformat()
        plants = [{"name": "Tomato", "frequency_days": 5,
                   "last_watered": last_watered, "location": "outdoor"}]

        agent = _make_agent_with_plants(plants, RAINY_HEATWAVE_WEATHER)
        result = agent._check_plants()

        assert "heatwave_timing" in result
        assert "Tomato" not in result["heatwave_timing"]

    def test_heatwave_timing_key_always_present(self):
        """heatwave_timing key must exist in result even when no plants."""
        agent = _make_agent_with_plants([], None)
        result = agent._check_plants()

        assert "heatwave_timing" in result
        assert result["heatwave_timing"] == []

    def test_indoor_plant_not_in_heatwave_timing(self):
        """Indoor plants are never added to heatwave_timing, even during a heatwave."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=2)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 5,
                   "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, HEATWAVE_WEATHER)
        result = agent._check_plants()

        assert result["heatwave_timing"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/cian/git/ai-agents
pytest tests/test_daily_briefing.py::TestHeatwaveTiming -v
```

Expected: `KeyError: 'heatwave_timing'` — key not yet in result dict.

- [ ] **Step 3: Update `agents/daily_briefing.py` — add `is_heatwave_incoming` import and `heatwave_timing`**

Change the import at line 14:

```python
from .plant_weather import adjust_watering_date, is_heatwave_incoming
```

Update `_check_plants()`:

**a)** In the `if plants is None` early return, add `"heatwave_timing": []`:

```python
        if plants is None:
            return {
                "plants": [],
                "tasks_to_create": [],
                "overwatering_risk": [],
                "heatwave_timing": [],
            }
```

**b)** Add `heatwave_timing = []` next to the other list declarations (around line 127):

```python
        upcoming_watering = []
        tasks_to_create = []
        overwatering_risk = []
        heatwave_timing = []
```

**c)** After the `effective_interval` + overwatering block (after the `if location == "indoor" and weather:` block), add:

```python
        if location == "outdoor" and weather and is_heatwave_incoming(weather) and days_until <= 1:
            heatwave_timing.append(plant["name"])
```

**d)** In the final `return` dict, add `"heatwave_timing": heatwave_timing`:

```python
        return {
            "plants": upcoming_watering,
            "tasks_to_create": tasks_to_create,
            "overwatering_risk": overwatering_risk,
            "heatwave_timing": heatwave_timing,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_daily_briefing.py::TestHeatwaveTiming -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Update `_build_prompt()` in `agents/daily_briefing.py`**

In `_build_prompt()`, after the `advisory_section` block, add:

```python
        heatwave_timing = plant_data.get("heatwave_timing", [])
        heatwave_section = ""
        if heatwave_timing:
            lines = ["⚠ Heatwave look-ahead — water these outdoor plants this evening or tomorrow morning before temperatures peak:"]
            for name in heatwave_timing:
                lines.append(f"- {name}")
            heatwave_section = "\n".join(lines)
```

In the returned f-string, add the Heatwave Timing section:

```python
        return f"""{base_prompt}

## Pre-computed Plant Data
{plant_section}

## Watering Advisory
{advisory_section if advisory_section else "No overwatering concerns."}

## Heatwave Timing
{heatwave_section if heatwave_section else "No heatwave look-ahead."}

## Plant Todoist Tasks
{task_instructions if task_instructions else "No plant tasks to create."}

## Today's Date
{today}
"""
```

- [ ] **Step 6: Update `agents/prompts/daily_briefing.md`**

Update Step 6 — add a sentence about heatwave timing after the Watering Advisory instruction:

Replace the last sentence of Step 6:
```
If the "Watering Advisory" section below contains entries (not "No overwatering concerns."), render the Watering Advisory HTML block in the email ABOVE the Plant Watering section.
```

With:
```
If the "Watering Advisory" section below contains entries (not "No overwatering concerns."), render the Watering Advisory HTML block in the email ABOVE the Plant Watering section.

If the "Heatwave Timing" section below is non-empty (not "No heatwave look-ahead."), add a heatwave timing note inside the Watering Advisory HTML block (or render the block solely for this note if no overwatering entries exist): *"Water these outdoor plants this evening or tomorrow morning before temperatures peak: [comma-separated list]."*
```

Also update the email HTML template — replace the existing Watering Advisory comment block:

```html
        <!-- Watering Advisory (render when advisory entries exist OR heatwave timing non-empty) -->
        [If advisory entries exist OR heatwave timing non-empty:
        <tr><td style="padding:20px 32px 0;">
          <div style="padding:12px 16px;background:#1c2128;border-left:3px solid #d29922;border-radius:4px;">
            <p style="margin:0 0 8px;font-size:11px;font-weight:700;text-transform:uppercase;color:#d29922;letter-spacing:0.5px;">🌡 Watering Advisory</p>
            [For each risk entry: <p style="margin:0 0 4px;font-size:13px;color:#c9d1d9;">[Plant Name]: next watering in [N]d (normal cycle: [M]d)</p>]
            [If risk entries exist: <p style="margin:8px 0 0;font-size:12px;color:#8b949e;font-style:italic;">Weather is driving more frequent watering than usual. Check soil before watering.</p>]
            [If heatwave timing non-empty: <p style="margin:8px 0 0;font-size:13px;color:#c9d1d9;">Water these outdoor plants <strong>this evening or tomorrow morning</strong> before temperatures peak: [list].</p>]
          </div>
        </td></tr>]
```

- [ ] **Step 7: Run the full daily briefing test suite**

```bash
pytest tests/test_daily_briefing.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add agents/daily_briefing.py agents/prompts/daily_briefing.md tests/test_daily_briefing.py
git commit -m "feat: add heatwave_timing to daily briefing — note before heatwave peaks"
```

---

### Task 4: Heatwave look-ahead task creation in `agents/plant_weather_agent.py`

Outdoor plants due tomorrow during a heatwave get a Todoist task created today with "Water [plant] before heatwave" content. This task lives alongside the normal same-day task creation; when `adjusted <= today` the plant already gets the normal "Water [plant]" task.

**Files:**
- Modify: `agents/plant_weather_agent.py`

No new tests needed here — `is_heatwave_incoming()` is already tested in Task 1. The agent logic flows directly from that helper.

- [ ] **Step 1: Update `_CREATE_TASK_PROMPT` to use `{content}` variable**

Replace the current `_CREATE_TASK_PROMPT` (lines 12–18):

```python
_CREATE_TASK_PROMPT = """\
Create a Todoist task in project {project_id} if it does not already exist:
  Content: "{content}", due: {due}, priority: p4

Use mcp__todoist__find-tasks to search for an existing task with content "{content}" \
due on {due}. Only call mcp__todoist__add-tasks if no such task exists.
Return only the word "created" or "exists".
"""
```

- [ ] **Step 2: Update the import in `agents/plant_weather_agent.py`**

Change line 7:

```python
from .plant_weather import adjust_watering_date, is_heatwave_incoming
```

- [ ] **Step 3: Update `_update_adjustments()` with heatwave look-ahead**

Replace the `if adjusted <= today:` block inside the `for plant in plants:` loop (currently lines 74–75):

```python
            if adjusted <= today:
                tasks_to_create.append({"name": plant["name"], "due": adjusted.isoformat()})
            elif (plant.get("location") == "outdoor"
                  and is_heatwave_incoming(weather)
                  and adjusted <= today + timedelta(days=1)):
                tasks_to_create.append({
                    "name": plant["name"],
                    "due": adjusted.isoformat(),
                    "heatwave": True,
                })
```

- [ ] **Step 4: Update `_create_tasks()` to use task content**

Replace the `prompt = _CREATE_TASK_PROMPT.format(...)` block inside `_create_tasks()` (currently lines 95–99):

```python
            name = task["name"]
            due = task["due"]
            is_heatwave_task = task.get("heatwave", False)
            content = f"Water {name} before heatwave" if is_heatwave_task else f"Water {name}"
            dedup_key = f"{name}:{today}"

            if self.is_duplicate("task_created", dedup_key):
                skipped += 1
                continue

            prompt = _CREATE_TASK_PROMPT.format(
                project_id=PERSONAL_PROJECT_ID,
                content=content,
                due=due,
            )
```

- [ ] **Step 5: Run the full test suite to check nothing broke**

```bash
cd /home/cian/git/ai-agents
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add agents/plant_weather_agent.py
git commit -m "feat: add heatwave look-ahead task creation to plant_weather_agent"
```

---

### Task 5: Add `research_plant_water_sensitivity()` to `telegram-bot/tools.py`

Same pattern as `research_plant_sunlight()` — calls Antigravity via stdin, validates against allowed values, defaults to "medium" if unclear.

**Files:**
- Modify: `telegram-bot/tools.py`
- Test: `telegram-bot/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Add to `telegram-bot/test_tools.py`:

Update the import at the top to include `research_plant_water_sensitivity` and `add_plant`:

```python
from tools import (
    get_agent_status,
    get_plant_status,
    get_yopflix_status,
    get_system_health,
    get_cron_schedule,
    get_agent_logs,
    water_plant,
    update_plant,
    remove_plant,
    save_recipe,
    get_all_plants,
    get_plant,
    save_plant_assessment,
    research_plant_watering,
    research_plant_sunlight,
    research_plant_water_sensitivity,
    add_plant,
)
```

Add test class at the bottom of the file:

```python
# ---------------------------------------------------------------------------
# research_plant_water_sensitivity
# ---------------------------------------------------------------------------

def test_research_plant_water_sensitivity_returns_valid_value(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="high\n")
    result = research_plant_water_sensitivity("Cactus")
    assert result == "high"
    assert "Cactus" in mock_run.call_args.kwargs["input"]


def test_research_plant_water_sensitivity_extracts_from_verbose_response(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="This cactus has high water sensitivity.\n")
    result = research_plant_water_sensitivity("Cactus")
    assert result == "high"


def test_research_plant_water_sensitivity_returns_medium(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="medium\n")
    result = research_plant_water_sensitivity("Pothos")
    assert result == "medium"


def test_research_plant_water_sensitivity_failure_returns_error(mocker):
    mock_run = mocker.patch("tools.subprocess.run")
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
    result = research_plant_water_sensitivity("Monstera")
    assert "could not" in result.lower() or "failed" in result.lower()


# ---------------------------------------------------------------------------
# add_plant — stores water_sensitivity
# ---------------------------------------------------------------------------

def test_add_plant_stores_water_sensitivity(mocker):
    """add_plant() auto-researches and stores water_sensitivity."""
    mock_db = MagicMock()
    mock_db.get_state.return_value = []
    mocker.patch("tools.research_plant_water_sensitivity", return_value="high")
    with patch("tools.AgentDB", return_value=mock_db):
        result = add_plant("Cactus", 14, location="indoor")
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["water_sensitivity"] == "high"
    assert "Cactus" in result


def test_add_plant_defaults_sensitivity_to_medium_on_research_failure(mocker):
    """If research returns a non-valid value, add_plant falls back to 'medium'."""
    mock_db = MagicMock()
    mock_db.get_state.return_value = []
    mocker.patch("tools.research_plant_water_sensitivity", return_value="Could not determine: timeout")
    with patch("tools.AgentDB", return_value=mock_db):
        add_plant("Mystery Plant", 7, location="indoor")
    saved = mock_db.set_state.call_args[0][2]
    assert saved[0]["water_sensitivity"] == "medium"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/cian/git/ai-agents/telegram-bot
pytest test_tools.py::test_research_plant_water_sensitivity_returns_valid_value test_tools.py::test_add_plant_stores_water_sensitivity -v
```

Expected: `ImportError` — `research_plant_water_sensitivity` not defined yet.

- [ ] **Step 3: Add `SENSITIVITY_VALUES` constant and `research_plant_water_sensitivity()` to `telegram-bot/tools.py`**

After the `SUNLIGHT_VALUES` constant (line 24), add:

```python
SENSITIVITY_VALUES = ("high", "medium", "low")
```

After the `research_plant_sunlight()` function, add:

```python
def research_plant_water_sensitivity(plant_name: str) -> str:
    prompt = (
        f"What is the water sensitivity of a {plant_name} plant? "
        "High sensitivity means it is very prone to overwatering (cacti, succulents, snake plant, ZZ plant). "
        "Low sensitivity means it prefers consistently moist soil (ferns, peace lily, carnivorous plants). "
        "Medium covers most common houseplants (pothos, monstera, philodendron). "
        "Reply with exactly one of: 'high', 'medium', or 'low'. "
        "Reply with only that word and nothing else."
    )
    try:
        res = subprocess.run(
            ["antigravity", "-y", "-o", "text"],
            input=prompt,
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT),
        )
        if res.returncode == 0 and res.stdout.strip():
            answer = res.stdout.strip().lower()
            for val in SENSITIVITY_VALUES:
                if val in answer:
                    return val
        return f"Could not determine water sensitivity: {res.stderr[:100]}"
    except Exception as e:
        return f"Research failed: {e}"
```

- [ ] **Step 4: Update `add_plant()` to auto-research and store `water_sensitivity`**

Replace the current `add_plant()` function:

```python
def add_plant(name: str, frequency_days: int, location: str = "indoor", sunlight: str = "") -> str:
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        name_lower = name.lower().strip()
        if any(p["name"].lower() == name_lower for p in plants):
            db.close()
            return f"A plant named '{name}' already exists."

        sensitivity = research_plant_water_sensitivity(name)
        if sensitivity not in SENSITIVITY_VALUES:
            sensitivity = "medium"

        plants.append({
            "name": name,
            "frequency_days": frequency_days,
            "last_watered": date.today().isoformat(),
            "location": location,
            "sunlight": sunlight,
            "water_sensitivity": sensitivity,
        })
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        sun_str = f", {sunlight}" if sunlight else ""
        return (
            f"{name} added ({location}{sun_str}, water every {frequency_days} days, "
            f"sensitivity: {sensitivity}). Last watered set to today."
        )
    except Exception as e:
        return f"Failed to add plant: {e}"
```

- [ ] **Step 5: Run the sensitivity tests**

```bash
cd /home/cian/git/ai-agents/telegram-bot
pytest test_tools.py -k "sensitivity or add_plant" -v
```

Expected: all 6 new tests PASS.

- [ ] **Step 6: Run the full telegram-bot test suite**

```bash
pytest test_tools.py -v
```

Expected: all tests PASS. (The existing `add_plant` test doesn't exist in the current file — `add_plant` is imported but not tested separately. The new tests cover it.)

- [ ] **Step 7: Commit**

```bash
git add telegram-bot/tools.py telegram-bot/test_tools.py
git commit -m "feat: auto-research water_sensitivity when adding plant via Telegram bot"
```

---

### Task 6: Update CLAUDE.md

The plant data model has a new field. Keep CLAUDE.md accurate.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the plant data model in the Plant Watering Tracker section**

Find the line:
```
- Plant data model: `{name, frequency_days, last_watered, location}` — location is `"indoor"` or `"outdoor"`
```

Replace with:
```
- Plant data model: `{name, frequency_days, last_watered, location, sunlight, water_sensitivity}` — location is `"indoor"` or `"outdoor"`, water_sensitivity is `"high"` / `"medium"` / `"low"` (auto-researched at add time; defaults to `"medium"` when absent)
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update plant data model in CLAUDE.md with water_sensitivity field"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| `water_sensitivity` field "high"/"medium"/"low" | Task 5 (add_plant stores it) |
| Auto-researched via Antigravity at add_plant() time | Task 5 |
| Sensitivity thresholds: high=0.8, medium=0.6, low=0.4 | Task 2 |
| Indoor-only overwatering check | Task 2 |
| `is_heatwave_incoming()` pure helper | Task 1 |
| Heatwave look-ahead in plant_weather_agent | Task 4 |
| Heatwave task content "Water [plant] before heatwave" | Task 4 |
| Rain self-cancels via positive outdoor adjustment | Handled by existing `_outdoor_adjustment()` + `is_heatwave_incoming()` False path |
| `heatwave_timing` list in _check_plants() | Task 3 |
| Heatwave timing note in email | Task 3 |
| Tests for is_heatwave_incoming | Task 1 |
| Tests for sensitivity thresholds | Task 2 |
| Tests for heatwave_timing | Task 3 |
| Tests for research_plant_water_sensitivity | Task 5 |
| Tests for add_plant stores sensitivity | Task 5 |
| CLAUDE.md updated | Task 6 |

All spec requirements covered. ✓

### No placeholders

No "TBD", "TODO", or vague steps. All code is shown. ✓

### Type consistency

- `is_heatwave_incoming(weather: dict) -> bool` — used in Task 1 (plant_weather.py), Task 3 (daily_briefing.py), Task 4 (plant_weather_agent.py). Import signature consistent across all tasks. ✓
- `heatwave_timing: list[str]` — added in Task 3, read in `_build_prompt()` in same task. ✓
- `SENSITIVITY_THRESHOLDS` dict — defined and used in Task 2 (`daily_briefing.py`). Not shared with `tools.py` (separate `SENSITIVITY_VALUES` tuple there — values vs. mapping). ✓
- `_CREATE_TASK_PROMPT` `{content}` variable — defined and used in Task 4 only. ✓
