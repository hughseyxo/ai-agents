# Overwatering Warning & Weather-Responsive Task Creation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the MIN_INTERVAL_DAYS floor that silently suppresses heat-driven watering adjustments, and add a "Watering Advisory" section to the daily briefing email when weather pushes plants to unusually short watering intervals.

**Architecture:** Pure function changes in `plant_weather.py` (remove floor), state computation changes in `daily_briefing.py` (`_check_plants` + `_build_prompt`), and an HTML template addition in `daily_briefing.md`. No schema changes. All changes are covered by existing test infrastructure.

**Tech Stack:** Python 3, pytest, SQLite (via AgentDB), Jinja-free HTML email template in Markdown

---

## File Map

| File | Change |
|------|--------|
| `agents/plant_weather.py` | Delete `MIN_INTERVAL_DAYS` constant + guard block in `adjust_watering_date()` |
| `agents/daily_briefing.py` | Add `overwatering_risk` detection in `_check_plants()`; inject it in `_build_prompt()` |
| `agents/prompts/daily_briefing.md` | Add Watering Advisory HTML block to email template |
| `tests/test_plant_weather.py` | Remove `test_never_reduces_interval_below_2_days`; add `test_heat_adjustment_applied_without_floor` |
| `tests/test_daily_briefing.py` | Add `TestOverwateringRisk` class |

---

### Task 1: Fix plant_weather tests (remove MIN_INTERVAL_DAYS test, add floor-free test)

**Files:**
- Modify: `tests/test_plant_weather.py:240-256`

- [ ] **Step 1: Delete the MIN_INTERVAL_DAYS test and add a replacement**

In `tests/test_plant_weather.py`, find the `TestAdjustWateringDate` class. Replace the `test_never_reduces_interval_below_2_days` test (currently lines 240–248) with a test that asserts the floor is GONE, and also replace `test_reason_string_describes_adjustment` to keep it accurate.

The full class after edits:

```python
class TestAdjustWateringDate:
    def test_no_adjustment_returns_base_date(self):
        base = date(2026, 5, 17)
        adjusted, reason = adjust_watering_date(
            base, 7, _plant(), _weather())
        assert adjusted == base
        assert reason == ""

    def test_hot_dry_shifts_date_earlier(self):
        base = date(2026, 5, 17)
        adjusted, reason = adjust_watering_date(
            base, 7, _plant(),
            _weather(temp_c=34, humidity_pct=30))
        assert adjusted < base
        assert "hot" in reason.lower() or "dry" in reason.lower()

    def test_cold_humid_shifts_date_later(self):
        base = date(2026, 5, 17)
        adjusted, reason = adjust_watering_date(
            base, 7, _plant(),
            _weather(temp_c=3, humidity_pct=90))
        assert adjusted > base
        assert "cold" in reason.lower() or "humid" in reason.lower()

    def test_heat_adjustment_applied_without_floor(self):
        # -2 adjustment on a 3-day cycle plant watered yesterday:
        # base = yesterday + 3 = today + 2
        # adjusted = today + 2 - 2 = today  (effective_interval = 1 day)
        # With MIN_INTERVAL_DAYS removed, this is allowed.
        yesterday = date(2026, 5, 9)
        base = date(2026, 5, 12)  # yesterday + 3
        plant = {
            "name": "Test Plant",
            "frequency_days": 3,
            "last_watered": yesterday.isoformat(),
            "location": "indoor",
        }
        adjusted, reason = adjust_watering_date(
            base, 3, plant,
            _weather(temp_c=34, humidity_pct=30))
        # Should be exactly base + adj (-2) = May 10, NOT clamped to May 11
        assert adjusted == date(2026, 5, 10)
        assert reason != ""

    def test_reason_string_describes_adjustment(self):
        base = date(2026, 5, 17)
        _, reason = adjust_watering_date(
            base, 7, _plant(location="outdoor"),
            _weather(recent_precip_mm=10))
        assert reason != ""
        assert "rain" in reason.lower()
```

- [ ] **Step 2: Run the new test — confirm it FAILS (because MIN_INTERVAL_DAYS still exists)**

```bash
cd /home/cian/git/ai-agents && pytest tests/test_plant_weather.py::TestAdjustWateringDate::test_heat_adjustment_applied_without_floor -v
```

Expected: `FAILED` — the current code clamps `adjusted` to `date(2026, 5, 11)` (2 days after yesterday), not `date(2026, 5, 10)`.

---

### Task 2: Remove MIN_INTERVAL_DAYS from plant_weather.py

**Files:**
- Modify: `agents/plant_weather.py:8-9,96-105`

- [ ] **Step 1: Delete the constant and guard block**

In `agents/plant_weather.py`, make the following changes:

**Delete lines 8–9** (the constant):
```python
MAX_ADJUSTMENT = 3
MIN_INTERVAL_DAYS = 2   # ← DELETE THIS LINE
```
becomes:
```python
MAX_ADJUSTMENT = 3
```

**Delete lines 96–105** (the guard block inside `adjust_watering_date()`). The block to remove is:
```python
    # Never reduce effective interval below MIN_INTERVAL_DAYS
    last_watered_str = plant.get("last_watered")
    if last_watered_str and adj < 0:
        from datetime import datetime
        last_watered = datetime.strptime(last_watered_str, "%Y-%m-%d").date()
        effective_interval = (adjusted - last_watered).days
        if effective_interval < MIN_INTERVAL_DAYS:
            adjusted = last_watered + timedelta(days=MIN_INTERVAL_DAYS)
            adj = (adjusted - base_date).days
            if adj == 0:
                return base_date, ""
```

After removal, `adjust_watering_date()` should look like:

```python
def adjust_watering_date(base_date: date, frequency_days: int,
                         plant: dict, weather: dict) -> tuple[date, str]:
    """Apply weather adjustment to a watering date.

    Returns (adjusted_date, reason_string).
    Reason is empty string if no adjustment was made.
    """
    adj = calculate_adjustment(plant, weather)

    if adj == 0:
        return base_date, ""

    adjusted = base_date + timedelta(days=adj)

    reason = _build_reason(adj, plant, weather)
    return adjusted, reason
```

- [ ] **Step 2: Run the failing test — now it should PASS**

```bash
cd /home/cian/git/ai-agents && pytest tests/test_plant_weather.py::TestAdjustWateringDate::test_heat_adjustment_applied_without_floor -v
```

Expected: `PASSED`

- [ ] **Step 3: Run the full plant_weather test suite**

```bash
cd /home/cian/git/ai-agents && pytest tests/test_plant_weather.py -v
```

Expected: All tests pass. `test_never_reduces_interval_below_2_days` no longer exists.

- [ ] **Step 4: Commit**

```bash
cd /home/cian/git/ai-agents && git add agents/plant_weather.py tests/test_plant_weather.py
git commit -m "feat: remove MIN_INTERVAL_DAYS floor — let weather drive watering dates freely"
```

---

### Task 3: Write failing tests for overwatering_risk detection

**Files:**
- Modify: `tests/test_daily_briefing.py`

- [ ] **Step 1: Add TestOverwateringRisk class to test_daily_briefing.py**

Append this class to the end of `tests/test_daily_briefing.py`:

```python
class TestOverwateringRisk:
    """_check_plants should populate overwatering_risk when effective interval
    is less than 60% of the plant's normal frequency."""

    def test_short_interval_flagged_as_overwatering_risk(self):
        """effective_interval < frequency * 0.6 → appears in overwatering_risk."""
        today = datetime.now(timezone.utc).date()
        # Plant just watered today, frequency 7, hot dry weather gives -2 adj:
        # base = today + 7, adjusted = today + 5, effective_interval = 5
        # threshold = 7 * 0.6 = 4.2 → 5 >= 4.2 → not triggered
        # Use a shorter frequency so the interval is clearly below threshold:
        # frequency_days=4, last_watered=today, adj=-2:
        #   base = today+4, adjusted = today+2, effective_interval=2
        #   threshold = 4*0.6 = 2.4 → 2 < 2.4 → TRIGGERED
        last_watered = today.isoformat()
        plants = [{"name": "Cactus", "frequency_days": 4,
                   "last_watered": last_watered, "location": "indoor"}]

        hot_dry = {
            "current": {"temp_c": 34, "humidity_pct": 30, "precip_mm": 0},
            "forecast": [
                {"date": "2026-05-15", "temp_max_c": 35, "precip_mm": 0},
                {"date": "2026-05-16", "temp_max_c": 33, "precip_mm": 0},
                {"date": "2026-05-17", "temp_max_c": 32, "precip_mm": 0},
            ],
            "recent_precip_mm": 0,
        }
        agent = _make_agent_with_plants(plants, hot_dry)
        result = agent._check_plants()

        assert "overwatering_risk" in result
        assert len(result["overwatering_risk"]) == 1
        risk = result["overwatering_risk"][0]
        assert risk["name"] == "Cactus"
        assert risk["effective_interval"] == 2
        assert risk["normal_frequency"] == 4

    def test_normal_interval_not_flagged(self):
        """effective_interval >= frequency * 0.6 → not in overwatering_risk."""
        today = datetime.now(timezone.utc).date()
        # Plant watered 8 days ago, frequency 10, no weather adjustment:
        # base = today+2, adjusted=today+2, effective_interval=10
        # threshold = 6 → 10 >= 6 → NOT triggered
        last_watered = (today - timedelta(days=8)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                   "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, NORMAL_WEATHER)
        result = agent._check_plants()

        assert "overwatering_risk" in result
        assert result["overwatering_risk"] == []

    def test_no_weather_no_risk(self):
        """Without weather, no adjustments → no overwatering risk."""
        today = datetime.now(timezone.utc).date()
        last_watered = today.isoformat()
        plants = [{"name": "Cactus", "frequency_days": 4,
                   "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, weather=None)
        result = agent._check_plants()

        assert result["overwatering_risk"] == []

    def test_overwatering_risk_key_always_present(self):
        """overwatering_risk key must be in result even when empty."""
        agent = _make_agent_with_plants([], NORMAL_WEATHER)
        result = agent._check_plants()
        assert "overwatering_risk" in result
        assert result["overwatering_risk"] == []
```

- [ ] **Step 2: Run the new tests — confirm they FAIL**

```bash
cd /home/cian/git/ai-agents && pytest tests/test_daily_briefing.py::TestOverwateringRisk -v
```

Expected: All 4 tests `FAILED` with `KeyError: 'overwatering_risk'` (key doesn't exist yet).

---

### Task 4: Add overwatering_risk detection to _check_plants()

**Files:**
- Modify: `agents/daily_briefing.py:118-162`

- [ ] **Step 1: Update _check_plants() to compute and return overwatering_risk**

Replace the entire `_check_plants` method with:

```python
def _check_plants(self):
    """Calculate watering schedule with weather adjustments."""
    plants = self.get_state("plants")
    if plants is None:
        return {"plants": [], "tasks_to_create": [], "overwatering_risk": []}

    weather = self.context.get("weather")
    today = datetime.now(timezone.utc).date()
    upcoming_watering = []
    tasks_to_create = []
    overwatering_risk = []

    for plant in plants:
        last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
        base_date = last_watered + timedelta(days=plant["frequency_days"])

        if weather:
            next_water, reason = adjust_watering_date(
                base_date, plant["frequency_days"], plant, weather)
        else:
            next_water = base_date
            reason = ""

        days_until = (next_water - today).days

        effective_interval = (next_water - last_watered).days
        if weather and effective_interval < plant["frequency_days"] * 0.6:
            overwatering_risk.append({
                "name": plant["name"],
                "effective_interval": effective_interval,
                "normal_frequency": plant["frequency_days"],
            })

        if days_until <= 7:
            entry = {
                "name": plant["name"],
                "next_water_date": next_water.isoformat(),
                "days_until": days_until,
            }
            if reason:
                entry["adjustment"] = reason
            upcoming_watering.append(entry)

        if days_until <= 0:
            tasks_to_create.append({
                "name": plant["name"],
                "task_content": f"Water {plant['name']}",
                "due_date": next_water.isoformat(),
            })

    return {
        "plants": upcoming_watering,
        "tasks_to_create": tasks_to_create,
        "overwatering_risk": overwatering_risk,
    }
```

- [ ] **Step 2: Run the TestOverwateringRisk tests — confirm they PASS**

```bash
cd /home/cian/git/ai-agents && pytest tests/test_daily_briefing.py::TestOverwateringRisk -v
```

Expected: All 4 tests `PASSED`.

- [ ] **Step 3: Run the full daily_briefing test suite**

```bash
cd /home/cian/git/ai-agents && pytest tests/test_daily_briefing.py -v
```

Expected: All tests pass (the key is now always present so existing tests are unaffected).

---

### Task 5: Inject Watering Advisory into the briefing prompt

**Files:**
- Modify: `agents/daily_briefing.py:189-226`

- [ ] **Step 1: Update _build_prompt() to include the advisory section**

Replace the entire `_build_prompt` method with:

```python
def _build_prompt(self, today: str, plant_data: dict) -> str:
    """Build the Claude CLI prompt, injecting pre-computed plant data."""
    prompt_path = REPO_ROOT / "agents" / "prompts" / "daily_briefing.md"
    base_prompt = prompt_path.read_text()

    plant_section = ""
    plants = plant_data.get("plants", [])
    tasks_to_create = plant_data.get("tasks_to_create", [])
    overwatering_risk = plant_data.get("overwatering_risk", [])

    if plants:
        plant_lines = []
        for p in plants:
            line = f"- {p['next_water_date']} — Water {p['name']} (in {p['days_until']} days)"
            if p.get("adjustment"):
                line += f" [adjusted: {p['adjustment']}]"
            plant_lines.append(line)
        plant_section = "\n".join(plant_lines)
    else:
        plant_section = "All plants are happy — no watering needed in the next 7 days."

    task_instructions = ""
    if tasks_to_create:
        task_lines = ["Create these Todoist tasks in project 6Crf3cH2RF5v86wc (if they don't already exist):"]
        for t in tasks_to_create:
            task_lines.append(f'- Content: "{t["task_content"]}", due: {t["due_date"]}, priority: p4')
        task_instructions = "\n".join(task_lines)

    advisory_section = ""
    if overwatering_risk:
        risk_lines = ["⚠ Watering Advisory — weather is driving unusually frequent watering for:"]
        for r in overwatering_risk:
            risk_lines.append(
                f"- {r['name']}: next watering in {r['effective_interval']}d "
                f"(normal cycle: {r['normal_frequency']}d)"
            )
        advisory_section = "\n".join(risk_lines)

    return f"""{base_prompt}

## Pre-computed Plant Data
{plant_section}

## Watering Advisory
{advisory_section if advisory_section else "No overwatering concerns."}

## Plant Todoist Tasks
{task_instructions if task_instructions else "No plant tasks to create."}

## Today's Date
{today}
"""
```

- [ ] **Step 2: Run the full test suite**

```bash
cd /home/cian/git/ai-agents && pytest tests/test_daily_briefing.py tests/test_plant_weather.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
cd /home/cian/git/ai-agents && git add agents/daily_briefing.py tests/test_daily_briefing.py
git commit -m "feat: add overwatering_risk detection and advisory prompt injection"
```

---

### Task 6: Add Watering Advisory HTML block to email template

**Files:**
- Modify: `agents/prompts/daily_briefing.md`

- [ ] **Step 1: Add advisory instructions to Step 6 and a new HTML block**

In `agents/prompts/daily_briefing.md`, update **Step 6** to include advisory rendering instructions. Replace the current Step 6 block:

```markdown
## Step 6: Handle plant watering tasks
The agent has pre-computed plant watering data (see "Pre-computed Plant Data" section below).
Tasks are only created on the morning of the watering day, so weather is always current.

If there are plant tasks to create, check if each task already exists in project 6Crf3cH2RF5v86wc first (search by content matching "Water [Plant Name]"), then create any missing ones via `mcp__todoist__add-tasks` with priority p4.
```

with:

```markdown
## Step 6: Handle plant watering tasks and advisory
The agent has pre-computed plant watering data (see "Pre-computed Plant Data" section below).
Tasks are only created on the morning of the watering day, so weather is always current.

If there are plant tasks to create, check if each task already exists in project 6Crf3cH2RF5v86wc first (search by content matching "Water [Plant Name]"), then create any missing ones via `mcp__todoist__add-tasks` with priority p4.

If the "Watering Advisory" section below contains entries (not "No overwatering concerns."), render the Watering Advisory HTML block in the email ABOVE the Plant Watering section.
```

- [ ] **Step 2: Add the advisory HTML block to the email template**

In the email template HTML (after the `<!-- Plant Watering -->` comment row and before the `<!-- Quick Wins -->` row), add the advisory block. Find this section in the template:

```html
        <!-- Plant Watering -->
        <tr><td style="padding:20px 32px 0;">
```

Insert the advisory block BEFORE the Plant Watering section:

```html
        <!-- Watering Advisory (render only when Watering Advisory section has entries) -->
        [If advisory entries exist:
        <tr><td style="padding:20px 32px 0;">
          <div style="padding:12px 16px;background:#1c2128;border-left:3px solid #d29922;border-radius:4px;">
            <p style="margin:0 0 8px;font-size:11px;font-weight:700;text-transform:uppercase;color:#d29922;letter-spacing:0.5px;">🌡 Watering Advisory</p>
            [For each risk entry: <p style="margin:0 0 4px;font-size:13px;color:#c9d1d9;">[Plant Name]: next watering in [N]d (normal cycle: [M]d)</p>]
            <p style="margin:8px 0 0;font-size:12px;color:#8b949e;font-style:italic;">Weather is driving more frequent watering than usual. Check soil before watering.</p>
          </div>
        </td></tr>]

        <!-- Plant Watering -->
```

- [ ] **Step 3: Run full test suite one final time**

```bash
cd /home/cian/git/ai-agents && pytest tests/test_plant_weather.py tests/test_daily_briefing.py -v
```

Expected: All tests pass.

- [ ] **Step 4: Final commit**

```bash
cd /home/cian/git/ai-agents && git add agents/prompts/daily_briefing.md
git commit -m "feat: add Watering Advisory section to daily briefing email template"
```

---

## Self-Review

**Spec coverage:**
- ✅ Remove MIN_INTERVAL_DAYS → Task 2
- ✅ PlantWeatherAgent task creation now unblocked (MIN_INTERVAL_DAYS was the only blocker; `_create_tasks` already fires when `adjusted <= today`) → Task 2 covers this implicitly
- ✅ `overwatering_risk` detection at 60% threshold → Task 4
- ✅ Prompt injection → Task 5
- ✅ Email template advisory block → Task 6
- ✅ Tests updated → Tasks 1, 3

**Placeholder scan:** No TBDs, all code blocks complete.

**Type consistency:** `overwatering_risk` list entries use `{"name": str, "effective_interval": int, "normal_frequency": int}` consistently across Task 4 (production) and Task 3 (tests).
