# Weather-Aware Auto-Adjusting Watering Frequency — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the plant intelligence run and the concierge bot change a plant's watering frequency, with weather folded into a single visible `frequency_days` that differentiates indoor/outdoor and accounts for shade tolerance.

**Architecture:** Each plant gains `baseline_frequency_days` (health-derived). The hourly `weather_update` step recomputes the effective `frequency_days = clamp(baseline + weather_delta, 1, 30)` deterministically, replacing the old transient due-date adjustment. The weather delta (existing `calculate_adjustment`) is extended with a shade-tolerance modifier. Intelligence applies bounded baseline changes via a `[FREQUENCY]` marker; the bot sets the baseline directly. All changes log to each plant profile's Frequency History table.

**Tech Stack:** Python 3.10, pytest, SQLite (`AgentDB`), existing pure-function module `agents/plant_weather.py`.

**Spec:** `docs/superpowers/specs/2026-06-01-weather-aware-frequency-design.md`

**Note on commits (concurrent agent):** Antigravity edits this repo concurrently. Every commit step MUST `git add` only the explicit files listed — never `git add -A`/`.`.

---

### Task 1: Shade-tolerance modifier in `calculate_adjustment`

**Files:**
- Modify: `agents/plant_weather.py` (add constants + `_sunlight_modifier`, wire into `calculate_adjustment`)
- Test: `tests/test_plant_weather.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_plant_weather.py`:
```python
class TestSunlightModifier:
    HOT = {"current": {"temp_c": 28, "humidity_pct": 50},
           "recent_precip_mm": 0.0,
           "forecast": [{"temp_max_c": 33, "precip_mm": 0.0},
                        {"temp_max_c": 34, "precip_mm": 0.0}]}
    RAINY = {"current": {"temp_c": 15, "humidity_pct": 80},
             "recent_precip_mm": 12.0,
             "forecast": [{"temp_max_c": 16, "precip_mm": 3.0}]}
    MILD = {"current": {"temp_c": 20, "humidity_pct": 55},
            "recent_precip_mm": 0.0,
            "forecast": [{"temp_max_c": 22, "precip_mm": 0.0}]}

    def test_full_sun_amplifies_heat_drying(self):
        assert calculate_adjustment({"location": "outdoor", "sunlight": "partial shade"}, self.HOT) == -2
        assert calculate_adjustment({"location": "outdoor", "sunlight": "full sun"}, self.HOT) == -3
        assert calculate_adjustment({"location": "outdoor", "sunlight": "shade"}, self.HOT) == -1

    def test_shade_defers_more_in_rain_clamped(self):
        # total rain 15mm -> base +3 (already at MAX)
        assert calculate_adjustment({"location": "outdoor", "sunlight": "partial shade"}, self.RAINY) == 3
        assert calculate_adjustment({"location": "outdoor", "sunlight": "shade"}, self.RAINY) == 3   # +1 clamped
        assert calculate_adjustment({"location": "outdoor", "sunlight": "full sun"}, self.RAINY) == 2  # -1

    def test_no_modifier_when_base_zero(self):
        for sun in ("full sun", "shade", "partial shade"):
            assert calculate_adjustment({"location": "outdoor", "sunlight": sun}, self.MILD) == 0

    def test_unknown_sunlight_no_modifier(self):
        assert calculate_adjustment({"location": "outdoor"}, self.HOT) == -2
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_plant_weather.py::TestSunlightModifier -v`
Expected: FAIL (full-sun/shade asserts return the unmodified base value).

- [ ] **Step 3: Implement**

In `agents/plant_weather.py`, after `MAX_ADJUSTMENT = 3` add:
```python
MIN_FREQUENCY = 1
MAX_FREQUENCY = 30
MAX_FREQUENCY_STEP = 2


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _sunlight_modifier(adj: int, plant: dict) -> int:
    """Shade tolerance modulates a non-zero weather adjustment.

    Full sun dries faster (amplify drying / dampen deferral); shade holds
    moisture (dampen drying / amplify deferral). Partial shade / unknown: no change.
    """
    if adj == 0:
        return 0
    sun = (plant.get("sunlight") or "").strip().lower()
    if adj < 0:  # drying — water sooner
        if sun == "full sun":
            return adj - 1
        if sun == "shade":
            return adj + 1
        return adj
    # adj > 0 — wetter, defer
    if sun == "shade":
        return adj + 1
    if sun == "full sun":
        return adj - 1
    return adj
```

Then replace the body of `calculate_adjustment`:
```python
def calculate_adjustment(plant: dict, weather: dict) -> int:
    """Return days to shift watering. Negative=earlier, positive=later."""
    location = plant.get("location", "indoor")

    if location == "outdoor":
        adj = _outdoor_adjustment(weather)
    else:
        adj = _indoor_adjustment(weather)

    adj = _sunlight_modifier(adj, plant)
    return max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, adj))
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_plant_weather.py -v`
Expected: PASS (new class + all existing tests).

- [ ] **Step 5: Commit**

```bash
git add agents/plant_weather.py tests/test_plant_weather.py
git commit -m "feat(plants): shade-tolerance modifier in weather adjustment"
```

---

### Task 2: `weather_adjusted_frequency` (fold weather into a frequency)

**Files:**
- Modify: `agents/plant_weather.py`
- Test: `tests/test_plant_weather.py`

- [ ] **Step 1: Write the failing tests**
```python
class TestWeatherAdjustedFrequency:
    HOT = TestSunlightModifier.HOT

    def test_folds_delta_full_sun(self):
        plant = {"location": "outdoor", "sunlight": "full sun", "baseline_frequency_days": 7}
        freq, reason = weather_adjusted_frequency(plant, self.HOT)
        assert freq == 4          # 7 + (-3)
        assert reason != ""

    def test_no_weather_returns_baseline(self):
        assert weather_adjusted_frequency({"baseline_frequency_days": 7, "location": "indoor"}, None) == (7, "")

    def test_clamps_min(self):
        plant = {"location": "outdoor", "sunlight": "full sun", "baseline_frequency_days": 2}
        freq, _ = weather_adjusted_frequency(plant, self.HOT)
        assert freq == 1          # 2-3 -> clamp 1

    def test_baseline_missing_falls_back_to_frequency_days(self):
        assert weather_adjusted_frequency({"frequency_days": 10, "location": "indoor"}, None) == (10, "")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_plant_weather.py::TestWeatherAdjustedFrequency -v`
Expected: FAIL (`weather_adjusted_frequency` undefined).

- [ ] **Step 3: Implement** — add to `agents/plant_weather.py` (below `adjust_watering_date`):
```python
def weather_adjusted_frequency(plant: dict, weather: dict | None) -> tuple[int, str]:
    """Effective frequency = clamp(baseline + weather delta, 1, 30).

    Returns (frequency_days, reason). reason is '' when no weather or no delta.
    """
    baseline = plant.get("baseline_frequency_days", plant["frequency_days"])
    if not weather:
        return _clamp(baseline, MIN_FREQUENCY, MAX_FREQUENCY), ""
    delta = calculate_adjustment(plant, weather)
    freq = _clamp(baseline + delta, MIN_FREQUENCY, MAX_FREQUENCY)
    reason = _build_reason(delta, plant, weather) if delta else ""
    return freq, reason
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_plant_weather.py::TestWeatherAdjustedFrequency -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agents/plant_weather.py tests/test_plant_weather.py
git commit -m "feat(plants): weather_adjusted_frequency folds weather into frequency"
```

---

### Task 3: `apply_frequency_step` (bounded baseline change)

**Files:**
- Modify: `agents/plant_weather.py`
- Test: `tests/test_plant_weather.py`

- [ ] **Step 1: Write the failing tests**
```python
class TestApplyFrequencyStep:
    def test_limits_decrease(self):
        assert apply_frequency_step(7, 3) == 5     # max -2 per call

    def test_limits_increase(self):
        assert apply_frequency_step(7, 12) == 9    # max +2 per call

    def test_reaches_close_target(self):
        assert apply_frequency_step(7, 6) == 6

    def test_clamps_bounds(self):
        assert apply_frequency_step(2, 0) == 1     # target clamped to 1
        assert apply_frequency_step(29, 40) == 30  # target clamped to 30
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_plant_weather.py::TestApplyFrequencyStep -v`
Expected: FAIL (`apply_frequency_step` undefined).

- [ ] **Step 3: Implement** — add to `agents/plant_weather.py`:
```python
def apply_frequency_step(current_baseline: int, target: int) -> int:
    """Move baseline toward target by at most MAX_FREQUENCY_STEP, clamped 1-30."""
    target = _clamp(int(target), MIN_FREQUENCY, MAX_FREQUENCY)
    if target > current_baseline:
        return min(target, current_baseline + MAX_FREQUENCY_STEP)
    return max(target, current_baseline - MAX_FREQUENCY_STEP)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_plant_weather.py::TestApplyFrequencyStep -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agents/plant_weather.py tests/test_plant_weather.py
git commit -m "feat(plants): bounded apply_frequency_step helper"
```

---

### Task 4: Recompute + migration in `weather_update`

**Files:**
- Modify: `agents/plant_agent.py` (import line 13; `_weather_update` ~line 138-155)
- Test: `tests/test_plant_agent.py`

- [ ] **Step 1: Write the failing tests** — add to `tests/test_plant_agent.py`:
```python
from datetime import date

class TestWeatherRecompute:
    HOT = {"current": {"temp_c": 28, "humidity_pct": 50},
           "recent_precip_mm": 0.0,
           "forecast": [{"temp_max_c": 33, "precip_mm": 0.0},
                        {"temp_max_c": 34, "precip_mm": 0.0}]}

    def _agent(self, plants, weather, monkeypatch):
        from agents import plant_agent as mod
        monkeypatch.setattr(mod, "fetch_weather", lambda: weather)
        a = mod.PlantAgent(db_path=":memory:")
        a.context = {"plan": {"plants": plants, "weather_cache": {}}}
        return a

    def test_migrates_baseline(self, monkeypatch):
        plants = [{"name": "X", "frequency_days": 7, "location": "indoor", "last_watered": "2026-05-31"}]
        self._agent(plants, None, monkeypatch)._weather_update()
        assert plants[0]["baseline_frequency_days"] == 7

    def test_folds_weather(self, monkeypatch):
        plants = [{"name": "X", "frequency_days": 7, "baseline_frequency_days": 7,
                   "location": "outdoor", "sunlight": "full sun", "last_watered": "2026-05-31"}]
        self._agent(plants, self.HOT, monkeypatch)._weather_update()
        assert plants[0]["frequency_days"] == 4   # 7-3

    def test_weather_failure_resets_to_baseline(self, monkeypatch):
        plants = [{"name": "X", "frequency_days": 4, "baseline_frequency_days": 7,
                   "location": "outdoor", "last_watered": "2026-05-31"}]
        self._agent(plants, None, monkeypatch)._weather_update()
        assert plants[0]["frequency_days"] == 7
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_plant_agent.py::TestWeatherRecompute -v`
Expected: FAIL (frequency unchanged / no baseline written).

- [ ] **Step 3: Implement**

In `agents/plant_agent.py` line 13, change the import:
```python
from .plant_weather import weather_adjusted_frequency, apply_frequency_step, is_heatwave_incoming
```

Replace the whole `_weather_update` method body with:
```python
    def _weather_update(self):
        weather = fetch_weather()
        plants = self.context["plan"]["plants"]
        changed = False
        for plant in plants:
            if "baseline_frequency_days" not in plant:
                plant["baseline_frequency_days"] = plant["frequency_days"]
                changed = True
            new_freq, reason = weather_adjusted_frequency(plant, weather)
            if new_freq != plant.get("frequency_days"):
                plant["frequency_days"] = new_freq
                changed = True
            last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
            next_date = last_watered + timedelta(days=plant["frequency_days"])
            self.db.upsert_plant_weather_cache(plant["name"], next_date.isoformat(), reason)
        if changed:
            self.db.set_state("daily-briefing", "plants", plants)
        return {"updated": len(plants)}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_plant_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agents/plant_agent.py tests/test_plant_agent.py
git commit -m "feat(plants): fold weather into frequency_days in weather_update + migrate baseline"
```

---

### Task 5: `create_tasks` uses folded frequency (extract `due_water_tasks`)

**Files:**
- Modify: `agents/plant_agent.py` (add module-level `due_water_tasks`; `_create_tasks` ~line 206-216)
- Test: `tests/test_plant_agent.py`

- [ ] **Step 1: Write the failing tests**
```python
from agents.plant_agent import due_water_tasks

class TestDueWaterTasks:
    HOT = TestWeatherRecompute.HOT  # heatwave: 2 days >30, dry

    def test_includes_overdue(self):
        today = date(2026, 6, 1)
        plants = [{"name": "X", "frequency_days": 4, "last_watered": "2026-05-27", "location": "indoor"}]
        assert due_water_tasks(plants, today, None) == [{"name": "X", "due": "2026-05-31"}]

    def test_excludes_future(self):
        today = date(2026, 6, 1)
        plants = [{"name": "Y", "frequency_days": 7, "last_watered": "2026-05-31", "location": "indoor"}]
        assert due_water_tasks(plants, today, None) == []

    def test_heatwave_creates_one_day_early(self):
        today = date(2026, 6, 1)
        plants = [{"name": "Z", "frequency_days": 3, "last_watered": "2026-05-30", "location": "outdoor"}]
        assert due_water_tasks(plants, today, self.HOT) == [{"name": "Z", "due": "2026-06-02", "heatwave": True}]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_plant_agent.py::TestDueWaterTasks -v`
Expected: FAIL (`due_water_tasks` undefined).

- [ ] **Step 3: Implement**

Add module-level function in `agents/plant_agent.py` (near the top, after imports / `_build_status_table`):
```python
def due_water_tasks(plants: list, today, weather) -> list:
    """Plants whose folded next-water date is due today/overdue (or +1 day for
    outdoor plants when a heatwave is incoming)."""
    tasks = []
    for plant in plants:
        last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
        due_date = last_watered + timedelta(days=plant["frequency_days"])
        if due_date <= today:
            tasks.append({"name": plant["name"], "due": due_date.isoformat()})
        elif (plant.get("location") == "outdoor" and weather
              and is_heatwave_incoming(weather)
              and due_date <= today + timedelta(days=1)):
            tasks.append({"name": plant["name"], "due": due_date.isoformat(), "heatwave": True})
    return tasks
```

In `_create_tasks`, replace the per-plant loop that builds `tasks_to_create` (the block using `adjust_watering_date`, ~lines 206-216) with a single call:
```python
        tasks_to_create = due_water_tasks(plants, today, weather)
```
Leave the existing `weather = fetch_weather()` line above it (needed for the heatwave check) and the downstream dedup/creation loop unchanged.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_plant_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agents/plant_agent.py tests/test_plant_agent.py
git commit -m "refactor(plants): create_tasks uses folded frequency via due_water_tasks"
```

---

### Task 6: Profile history helper + intelligence `[FREQUENCY]` application

**Files:**
- Create: `agents/plant_profiles.py`
- Modify: `agents/plant_agent.py` (`_apply_intelligence_output`; imports)
- Test: `tests/test_plant_profiles.py` (new), `tests/test_plant_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_plant_profiles.py`:
```python
def test_append_frequency_history_inserts_row(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    p = tmp_path / "lantana.md"
    p.write_text("# Lantana\n\n## Frequency History\n| Date | Change | Reason |\n|---|---|---|\n\n## Notes\n")
    assert pp.append_frequency_history("Lantana", 7, 5, "intelligence: wilting") is True
    txt = p.read_text()
    assert "7→5 days" in txt
    assert "intelligence: wilting" in txt

def test_append_frequency_history_missing_profile(tmp_path, monkeypatch):
    from agents import plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    assert pp.append_frequency_history("Ghost", 7, 5, "x") is False
```

Add to `tests/test_plant_agent.py`:
```python
class TestIntelligenceFrequency:
    def test_applies_with_step_limit_and_logs(self, tmp_path, monkeypatch):
        from agents import plant_agent as mod
        from agents import plant_profiles as pp
        monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
        monkeypatch.setattr(mod, "fetch_weather", lambda: None)
        (tmp_path / "lantana.md").write_text(
            "# Lantana\n## Frequency History\n| Date | Change | Reason |\n|---|---|---|\n")
        a = mod.PlantAgent(db_path=":memory:")
        plants = [{"name": "Lantana", "frequency_days": 7, "baseline_frequency_days": 7,
                   "location": "outdoor", "last_watered": "2026-05-31"}]
        a.context = {"plan": {"plants": plants}}
        a._apply_intelligence_output("[FREQUENCY]\nLantana — 3 — wilting\n[/FREQUENCY]", plants)
        assert plants[0]["baseline_frequency_days"] == 5   # 7 -> 5 (step -2)
        assert plants[0]["frequency_days"] == 5            # no weather -> baseline
        assert "7→5 days" in (tmp_path / "lantana.md").read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_plant_profiles.py tests/test_plant_agent.py::TestIntelligenceFrequency -v`
Expected: FAIL (`agents.plant_profiles` missing; `[FREQUENCY]` not handled).

- [ ] **Step 3: Implement**

Create `agents/plant_profiles.py`:
```python
"""File-I/O helpers for plant profile docs (docs/plants/<slug>.md)."""

from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLANTS_DIR = REPO_ROOT / "docs" / "plants"

_TABLE_HEADER = "| Date | Change | Reason |\n|---|---|---|\n"


def profile_path(plant_name: str) -> Path:
    slug = plant_name.lower().replace(" ", "-").replace("/", "-")
    return PLANTS_DIR / f"{slug}.md"


def append_frequency_history(plant_name: str, old: int, new: int, reason: str) -> bool:
    """Insert a row into the plant profile's Frequency History table.
    Returns False if the profile doc does not exist."""
    path = profile_path(plant_name)
    if not path.exists():
        return False
    content = path.read_text()
    today = datetime.now(timezone.utc).date().isoformat()
    row = f"| {today} | {old}→{new} days | {reason} |\n"
    if _TABLE_HEADER in content:
        content = content.replace(_TABLE_HEADER, _TABLE_HEADER + row, 1)
    elif "## Frequency History" in content:
        content = content.replace("## Frequency History\n",
                                  "## Frequency History\n" + _TABLE_HEADER + row, 1)
    else:
        content += f"\n## Frequency History\n{_TABLE_HEADER}{row}"
    path.write_text(content)
    return True
```

In `agents/plant_agent.py`, extend the import line from Task 4 to include `weather_adjusted_frequency` (already there) and add the profile import near the other imports:
```python
from .plant_profiles import append_frequency_history
```

In `_apply_intelligence_output`, after the existing `[PRUNING]` handling block, add:
```python
        freq_m = re.search(r'\[FREQUENCY\](.*?)\[/FREQUENCY\]', output, re.DOTALL)
        if freq_m:
            changed = False
            for line in freq_m.group(1).strip().splitlines():
                line = line.strip()
                if " — " not in line:
                    continue
                parts = [s.strip() for s in line.split(" — ")]
                if len(parts) < 2:
                    continue
                name = parts[0]
                try:
                    target = int(parts[1])
                except ValueError:
                    continue
                note = parts[2] if len(parts) > 2 else ""
                plant = next((p for p in plants if p["name"].lower() == name.lower()), None)
                if not plant:
                    continue
                old = plant.get("baseline_frequency_days", plant["frequency_days"])
                new = apply_frequency_step(old, target)
                if new != old:
                    plant["baseline_frequency_days"] = new
                    plant["frequency_days"], _ = weather_adjusted_frequency(plant, fetch_weather())
                    append_frequency_history(plant["name"], old, new, f"intelligence: {note}".rstrip(": ").strip())
                    changed = True
            if changed:
                self.db.set_state("daily-briefing", "plants", plants)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_plant_profiles.py tests/test_plant_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agents/plant_profiles.py agents/plant_agent.py tests/test_plant_profiles.py tests/test_plant_agent.py
git commit -m "feat(plants): intelligence applies bounded [FREQUENCY] changes with history logging"
```

---

### Task 7: Intelligence prompt — `[FREQUENCY]` marker instructions

**Files:**
- Modify: `agents/prompts/plant_intelligence.md`
- Test: `tests/test_plant_agent.py` (prompt guard)

- [ ] **Step 1: Write the failing test**
```python
def test_intelligence_prompt_documents_frequency_marker():
    from pathlib import Path
    import agents.plant_agent as mod
    text = (mod.REPO_ROOT / "agents" / "prompts" / "plant_intelligence.md").read_text()
    assert "[FREQUENCY]" in text and "[/FREQUENCY]" in text
    assert "baseline" in text.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_plant_agent.py::test_intelligence_prompt_documents_frequency_marker -v`
Expected: FAIL (marker not in prompt).

- [ ] **Step 3: Implement** — append a section to `agents/prompts/plant_intelligence.md`:
```markdown

## Adjusting watering frequency (optional)

If observations (photos, health assessments, recent weather trend) indicate a plant's
**baseline** watering cadence should change, emit a `[FREQUENCY]` block. One line per
plant: `PlantName — <new_baseline_days> — <short reason>`. Only include plants that
genuinely need a change.

- This sets the plant's **baseline** frequency (the system folds current weather in
  automatically — do NOT pre-adjust for today's weather here).
- Changes are clamped to 1–30 days and limited to a ±2-day step per run; large moves
  converge over several runs.

Example:
[FREQUENCY]
Lantana — 5 — wilting under full sun, soil dry before day 7
[/FREQUENCY]
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_plant_agent.py::test_intelligence_prompt_documents_frequency_marker -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agents/prompts/plant_intelligence.md tests/test_plant_agent.py
git commit -m "feat(plants): document [FREQUENCY] marker in intelligence prompt"
```

---

### Task 8: Concierge bot `set_plant_frequency` tool

**Files:**
- Modify: `telegram-bot/tools.py` (add `set_plant_frequency`)
- Modify: `telegram-bot/tool_specs.py` (import + SPEC entry)
- Test: `telegram-bot/test_tools.py`

- [ ] **Step 1: Write the failing test** — add to `telegram-bot/test_tools.py`:
```python
def test_set_plant_frequency_clamps_and_logs(tmp_path, monkeypatch):
    import tools
    from agents.db import AgentDB
    # in-memory DB seeded with one plant
    db = AgentDB(":memory:")
    db.set_state("daily-briefing", "plants",
                 [{"name": "Lantana", "frequency_days": 7, "location": "outdoor", "last_watered": "2026-05-31"}])
    monkeypatch.setattr(tools, "AgentDB", lambda *a, **k: db)
    monkeypatch.setattr(tools, "fetch_weather", lambda: None, raising=False)
    import agents.plant_profiles as pp
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
    (tmp_path / "lantana.md").write_text("# Lantana\n## Frequency History\n| Date | Change | Reason |\n|---|---|---|\n")

    out = tools.set_plant_frequency("Lantana", 99, "user request")
    plants = db.get_state("daily-briefing", "plants")
    assert plants[0]["baseline_frequency_days"] == 30      # clamped
    assert "Lantana" in out
    assert "30" in out
    assert "7→30 days" in (tmp_path / "lantana.md").read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd telegram-bot && python3 -m pytest test_tools.py::test_set_plant_frequency_clamps_and_logs -v`
Expected: FAIL (`set_plant_frequency` undefined).

- [ ] **Step 3: Implement**

In `telegram-bot/tools.py`, add near the top with the other agent imports:
```python
from agents.plant_weather import weather_adjusted_frequency, MIN_FREQUENCY, MAX_FREQUENCY
from agents.weather import fetch_weather
from agents.plant_profiles import append_frequency_history
```
Add the function (mirror `update_plant`'s structure):
```python
def set_plant_frequency(plant_name: str, frequency_days: int, reason: str = "") -> str:
    """Set a plant's BASELINE watering frequency (1-30 days). Weather is folded
    into the effective schedule automatically. Logs the change to the profile."""
    try:
        db = AgentDB(DB_PATH)
        plants = db.get_state("daily-briefing", "plants") or []
        match = _find_plant(plant_name, plants)
        if not match:
            names = ", ".join(p["name"] for p in plants)
            db.close()
            return f"No plant named '{plant_name}' found. Known plants: {names or 'none'}"
        target = max(MIN_FREQUENCY, min(MAX_FREQUENCY, int(frequency_days)))
        old = match.get("baseline_frequency_days", match["frequency_days"])
        match["baseline_frequency_days"] = target
        match["frequency_days"], _ = weather_adjusted_frequency(match, fetch_weather())
        db.set_state("daily-briefing", "plants", plants)
        db.close()
        if old != target:
            append_frequency_history(match["name"], old, target, f"bot: {reason}".rstrip(": ").strip())
        eff = match["frequency_days"]
        suffix = f" (effective {eff}d after weather)" if eff != target else ""
        return f"{match['name']} base frequency set to {target} days{suffix}."
    except Exception as e:
        return f"Failed to set frequency: {e}"
```

In `telegram-bot/tool_specs.py`, add `set_plant_frequency` to the imports from `tools` (alongside `update_plant`), and add a SPEC entry next to `update_plant`'s:
```python
    {
        "name": "set_plant_frequency",
        "description": "Set a plant's baseline watering frequency in days (1-30). Weather is folded into the effective schedule automatically. Use when the user wants to change how often a plant is watered.",
        "parameters": {
            "type": "object",
            "properties": {
                "plant_name": {"type": "string", "description": "Name of the plant (e.g. 'Lantana')."},
                "frequency_days": {"type": "integer", "description": "New baseline watering interval in days (1-30)."},
                "reason": {"type": "string", "description": "Short reason for the change (optional)."},
            },
            "required": ["plant_name", "frequency_days"],
        },
        "func": set_plant_frequency,
    },
```

- [ ] **Step 4: Run to verify pass**

Run: `cd telegram-bot && python3 -m pytest test_tools.py test_tool_specs.py -v`
Expected: PASS (tool test + canonical-spec consistency test).

- [ ] **Step 5: Commit**
```bash
git add telegram-bot/tools.py telegram-bot/tool_specs.py telegram-bot/test_tools.py
git commit -m "feat(concierge): set_plant_frequency tool (baseline + weather fold + history)"
```

---

### Task 9: Docs — CLAUDE.md, spec note, structure

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-06-01-weather-aware-frequency-design.md`

- [ ] **Step 1: Update CLAUDE.md**

In the Plant Watering Tracker section, change the data-model line to note baseline vs effective:
```
- Plant data model: `{name, baseline_frequency_days, frequency_days, last_watered, location, sunlight, water_sensitivity}` — `baseline_frequency_days` is the intrinsic cadence (set by intelligence/bot); `frequency_days` is the effective value recomputed hourly = clamp(baseline + weather delta, 1, 30), folding indoor/outdoor + shade tolerance. Migrated automatically (baseline defaults to frequency_days).
```
Add `agents/plant_profiles.py` to the Project Structure agents list:
```
│   ├── plant_profiles.py       # File-I/O helpers for docs/plants/<slug>.md (frequency history logging)
```
Add to the tests list:
```
│   ├── test_plant_profiles.py  # Frequency-history table append tests
```

- [ ] **Step 2: Add cache note to the spec**

In the design spec's "Consumers to update" section, append:
```
- `plant_weather_cache` (DB) is retained: `weather_update` writes `adjusted_date =
  last_watered + effective frequency_days` and the weather `reason`. `get_plant_status`
  and `_build_status_table` read this cache unchanged, so they show the folded date.
```

- [ ] **Step 3: Commit**
```bash
git add CLAUDE.md docs/superpowers/specs/2026-06-01-weather-aware-frequency-design.md
git commit -m "docs: weather-aware frequency model (CLAUDE.md + spec cache note)"
```

---

### Task 10: Full verification

- [ ] **Step 1: Run the entire suite**

Run: `python3 -m pytest -q && (cd telegram-bot && python3 -m pytest -q)`
Expected: all pass.

- [ ] **Step 2: Live smoke (open intelligence gate, run, confirm)**

```bash
python3 -c "from agents.db import AgentDB; AgentDB('data/agents.db').set_state('plant-agent','last_intelligence_run','2000-01-01T00:00:00+00:00')"
./run-agent.sh plant-agent 2>&1 | tail -5
./plant.sh list
```
Expected: agent runs clean; `plant.sh list` shows weather-folded `frequency_days`; any intelligence frequency change appears in the relevant `docs/plants/<slug>.md` Frequency History table.

- [ ] **Step 3: Final commit (if any doc/agent-notes changes from the live run)**
```bash
git add docs/plants
git commit -m "chore(plants): intelligence frequency notes from verification run"
```

---

## Self-Review

**Spec coverage:**
- Data model + migration → Task 4 (migration), Task 9 (docs). ✓
- Hourly weather recompute → Task 4. ✓
- Indoor vs outdoor (preserved) → unchanged `_indoor/_outdoor_adjustment`, verified by existing tests kept green in Task 1. ✓
- Shade tolerance → Task 1. ✓
- Intelligence auto-within-bounds (clamp 1–30, ±2 step, logged, reversible) → Task 3 + Task 6. ✓
- `[FREQUENCY]` prompt marker → Task 7. ✓
- Concierge bot tool → Task 8. ✓
- Consumers (drop `adjust_watering_date` in flow; cache retained) → Task 4, Task 5; cache note Task 9. ✓
- Testing → each task is TDD. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type/name consistency:** `weather_adjusted_frequency` (Task 2) → used Task 4/6/8; `apply_frequency_step` (Task 3) → Task 6; `due_water_tasks` (Task 5); `append_frequency_history`/`PLANTS_DIR`/`profile_path` (Task 6) → Task 8. `baseline_frequency_days` used consistently. Constants `MIN_FREQUENCY`/`MAX_FREQUENCY`/`MAX_FREQUENCY_STEP` defined Task 1, used Task 2/3/8.

**Note:** `adjust_watering_date` and its tests in `tests/test_plant_weather.py` are intentionally kept (still a valid pure function) but are no longer called by the main flow — no deletion needed (YAGNI).
