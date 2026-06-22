"""Tests for agents.plant_agent — gate logic, short-circuit, rate limiting, output parsing."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent(tmp_path):
    from agents.plant_agent import PlantAgent
    a = PlantAgent(db_path=tmp_path / "test.db")
    a.run_id = a.db.start_run(a.name)
    return a


def _make_plant(name="Fern", frequency_days=7, last_watered="2026-05-01",
                location="indoor", needs_photo=False):
    return {
        "name": name,
        "frequency_days": frequency_days,
        "last_watered": last_watered,
        "location": location,
        "sunlight": "indirect",
        "water_sensitivity": "medium",
        "needs_photo": needs_photo,
    }


# ---------------------------------------------------------------------------
# 1. _gate() logic
# ---------------------------------------------------------------------------

class TestGate:
    def test_returns_true_when_no_state_exists(self, agent):
        assert agent._gate("weather", 12) is True

    def test_returns_true_when_last_ran_beyond_window(self, agent):
        # Store a timestamp > 13 hours ago; gate window = 12h
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
        agent.set_state("last_weather", old_ts)
        assert agent._gate("weather", 12) is True

    def test_returns_false_when_last_ran_within_window(self, agent):
        # Store a timestamp 1 hour ago; gate window = 12h
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        agent.set_state("last_weather", recent_ts)
        assert agent._gate("weather", 12) is False

    def test_returns_true_exactly_at_boundary(self, agent):
        # _gate uses strict >, so exactly 12h elapsed returns True (just crossed the threshold)
        # In practice there are always a few ms of execution time, so this is rarely False.
        # We test the edge: exactly 12h - 1s → still within window → False.
        just_under_ts = (datetime.now(timezone.utc) - timedelta(hours=12) + timedelta(seconds=1)).isoformat()
        agent.set_state("last_weather", just_under_ts)
        assert agent._gate("weather", 12) is False

    def test_naive_datetime_treated_as_utc(self, agent):
        # Naive ISO string (no tzinfo) — stored without 'Z' suffix
        old_naive = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(tzinfo=None).isoformat()
        agent.set_state("last_sync", old_naive)
        assert agent._gate("sync", 24) is True


# ---------------------------------------------------------------------------
# 3b. _build_status_table() — chronological ordering
# ---------------------------------------------------------------------------

class TestBuildStatusTable:
    today = datetime(2026, 6, 1, tzinfo=timezone.utc).date()

    def _rows(self, table):
        # Drop header + separator, return the plant-name column of each data row
        lines = [l for l in table.splitlines() if l.startswith("|")][2:]
        return [l.split("|")[1].strip() for l in lines]

    def test_rows_sorted_by_next_water_date(self):
        from agents.plant_agent import _build_status_table
        plants = [
            _make_plant(name="Late", frequency_days=10, last_watered="2026-05-30"),    # next 2026-06-09
            _make_plant(name="Soon", frequency_days=3, last_watered="2026-05-30"),      # next 2026-06-02
            _make_plant(name="Overdue", frequency_days=2, last_watered="2026-05-25"),   # next 2026-05-27
        ]
        table = _build_status_table(plants, {}, self.today)
        assert self._rows(table) == ["Overdue", "Soon", "Late"]

    def test_weather_adjusted_date_drives_order(self):
        from agents.plant_agent import _build_status_table
        plants = [
            _make_plant(name="Base", frequency_days=2, last_watered="2026-05-31"),  # next 2026-06-02
            _make_plant(name="Adjusted", frequency_days=10, last_watered="2026-05-31"),
        ]
        cache = {"Adjusted": {"adjusted_date": "2026-06-01", "adjustment_reason": "heatwave"}}
        table = _build_status_table(plants, cache, self.today)
        assert self._rows(table) == ["Adjusted", "Base"]

    def test_ties_broken_by_name(self):
        from agents.plant_agent import _build_status_table
        plants = [
            _make_plant(name="Zinnia", frequency_days=5, last_watered="2026-05-30"),
            _make_plant(name="Aster", frequency_days=5, last_watered="2026-05-30"),
        ]
        table = _build_status_table(plants, {}, self.today)
        assert self._rows(table) == ["Aster", "Zinnia"]

    def test_bad_last_watered_skips_plant_not_crash(self):
        # One malformed date must not abort the whole table (per-plant guard).
        from agents.plant_agent import _build_status_table
        plants = [
            _make_plant(name="Good", frequency_days=3, last_watered="2026-05-30"),
            _make_plant(name="Bad", frequency_days=3, last_watered="not-a-date"),
        ]
        table = _build_status_table(plants, {}, self.today)
        assert self._rows(table) == ["Good"]

    def test_no_plants_returns_placeholder(self):
        from agents.plant_agent import _build_status_table
        assert _build_status_table([], {}, self.today) == "No plants tracked."


# ---------------------------------------------------------------------------
# 4. _apply_intelligence_output() — output parsing
# ---------------------------------------------------------------------------

class TestApplyIntelligenceOutput:

    def _make_profile(self, path: Path, plant: dict):
        """Write a minimal profile doc to path."""
        from agents.plant_agent import _create_profile_doc
        _create_profile_doc(path, plant)

    def test_profile_block_appends_notes_to_existing_doc(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Peace Lily")
        profile_path = plants_dir / "peace-lily.md"
        self._make_profile(profile_path, plant)
        assert "## Intelligence Notes" in profile_path.read_text()

        output = json.dumps({
            "plants": [{"name": "Peace Lily", "status": "Healthy",
                        "notes": ["Leaves look healthy, no yellowing."],
                        "needs_photo": False, "frequency_change": None}],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })

        with patch("agents.plant_agent.REPO_ROOT", tmp_path), \
                patch("agents.plant_profiles.PLANTS_DIR", plants_dir):
            agent._apply_intelligence_output(output, [plant])

        content = profile_path.read_text()
        assert "Leaves look healthy, no yellowing." in content

    def test_profile_block_creates_doc_if_missing(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Spider Plant")
        profile_path = plants_dir / "spider-plant.md"
        assert not profile_path.exists()

        output = json.dumps({
            "plants": [{"name": "Spider Plant", "status": "Healthy",
                        "notes": ["First observation: trailing stems."],
                        "needs_photo": False, "frequency_change": None}],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })

        with patch("agents.plant_agent.REPO_ROOT", tmp_path), \
                patch("agents.plant_profiles.PLANTS_DIR", plants_dir):
            agent._apply_intelligence_output(output, [plant])

        assert profile_path.exists()
        content = profile_path.read_text()
        assert "First observation: trailing stems." in content

    def test_profile_block_skipped_for_unknown_plant(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        output = json.dumps({
            "plants": [{"name": "Ghost Plant", "status": "Healthy",
                        "notes": ["Some notes."], "needs_photo": False, "frequency_change": None}],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })
        profile_path = plants_dir / "ghost-plant.md"

        with patch("agents.plant_agent.REPO_ROOT", tmp_path), \
                patch("agents.plant_profiles.PLANTS_DIR", plants_dir):
            agent._apply_intelligence_output(output, [])  # empty plant list

        # No doc created if plant not in list
        assert not profile_path.exists()

    def test_needs_photo_sets_flag_on_matching_plant(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Orchid", needs_photo=False)

        output = json.dumps({
            "plants": [{"name": "Orchid", "status": "Concerning",
                        "notes": [], "needs_photo": True, "frequency_change": None}],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

        plants_in_db = agent.db.get_state("daily-briefing", "plants")
        assert plants_in_db is not None
        orchid = next(p for p in plants_in_db if p["name"] == "Orchid")
        assert orchid["needs_photo"] is True

    def test_needs_photo_explicit_false_clears_flag(self, agent, tmp_path):
        """Plant with needs_photo=True gets cleared when JSON explicitly sets it False."""
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Cactus", needs_photo=True)

        output = json.dumps({
            "plants": [{"name": "Cactus", "status": "Healthy",
                        "notes": [], "needs_photo": False, "frequency_change": None}],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

        plants_in_db = agent.db.get_state("daily-briefing", "plants")
        cactus = next(p for p in plants_in_db if p["name"] == "Cactus")
        assert cactus["needs_photo"] is False

    def test_unlisted_plant_flag_unchanged(self, agent, tmp_path):
        """Plant not mentioned in JSON output keeps its existing needs_photo flag."""
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        plant = _make_plant(name="Fern", needs_photo=True)
        agent.db.set_state("daily-briefing", "plants", [plant])

        output = json.dumps({
            "plants": [],  # Fern not mentioned
            "pruning": [], "tasks_created": [], "email_sent": False,
        })

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output(output, [plant])

        plants_in_db = agent.db.get_state("daily-briefing", "plants")
        assert plants_in_db[0]["needs_photo"] is True

    def test_invalid_json_logs_and_skips(self, agent, tmp_path, capsys):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        with patch("agents.plant_agent.REPO_ROOT", tmp_path):
            agent._apply_intelligence_output("not valid json", [])

        captured = capsys.readouterr()
        assert "parse failed" in captured.err

    def test_multiple_profile_blocks_all_applied(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)

        fern = _make_plant(name="Fern")
        cactus = _make_plant(name="Cactus")
        for plant in [fern, cactus]:
            self._make_profile(plants_dir / f"{plant['name'].lower()}.md", plant)

        output = json.dumps({
            "plants": [
                {"name": "Fern", "status": "Healthy", "notes": ["Fern looks lush."],
                 "needs_photo": False, "frequency_change": None},
                {"name": "Cactus", "status": "Healthy", "notes": ["Cactus is thriving."],
                 "needs_photo": False, "frequency_change": None},
            ],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })

        with patch("agents.plant_agent.REPO_ROOT", tmp_path), \
                patch("agents.plant_profiles.PLANTS_DIR", plants_dir):
            agent._apply_intelligence_output(output, [fern, cactus])

        assert "Fern looks lush." in (plants_dir / "fern.md").read_text()
        assert "Cactus is thriving." in (plants_dir / "cactus.md").read_text()


# ---------------------------------------------------------------------------
# Weather recompute + baseline migration (Task 4)
# ---------------------------------------------------------------------------

from datetime import date


class TestWeatherRecompute:
    HOT = {"current": {"temp_c": 28, "humidity_pct": 50},
           "recent_precip_mm": 0.0,
           "forecast": [{"temp_max_c": 33, "precip_mm": 0.0},
                        {"temp_max_c": 34, "precip_mm": 0.0}]}

    def _agent(self, plants, weather, monkeypatch, tmp_path):
        from agents import plant_agent as mod
        monkeypatch.setattr(mod, "fetch_weather", lambda: weather)
        a = mod.PlantAgent(db_path=tmp_path / "wr.db")
        a.context = {"plan": {"plants": plants, "weather_cache": {}}}
        return a

    def test_migrates_baseline(self, monkeypatch, tmp_path):
        plants = [{"name": "X", "frequency_days": 7, "location": "indoor", "last_watered": "2026-05-31"}]
        self._agent(plants, None, monkeypatch, tmp_path)._weather_update()
        assert plants[0]["baseline_frequency_days"] == 7

    def test_folds_weather(self, monkeypatch, tmp_path):
        plants = [{"name": "X", "frequency_days": 7, "baseline_frequency_days": 7,
                   "location": "outdoor", "sunlight": "full sun", "last_watered": "2026-05-31"}]
        self._agent(plants, self.HOT, monkeypatch, tmp_path)._weather_update()
        assert plants[0]["frequency_days"] == 4   # 7-3

    def test_weather_failure_preserves_adjusted_frequency(self, monkeypatch, tmp_path):
        # fetch_weather() returning None must NOT revert frequency_days to baseline
        plants = [{"name": "X", "frequency_days": 4, "baseline_frequency_days": 7,
                   "location": "outdoor", "last_watered": "2026-05-31"}]
        self._agent(plants, None, monkeypatch, tmp_path)._weather_update()
        assert plants[0]["frequency_days"] == 4


# ---------------------------------------------------------------------------
# Intelligence [FREQUENCY] application (Task 6)
# ---------------------------------------------------------------------------

class TestIntelligenceFrequency:
    def test_applies_with_step_limit_and_logs(self, tmp_path, monkeypatch):
        from agents import plant_agent as mod
        from agents import plant_profiles as pp
        monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path)
        monkeypatch.setattr(mod, "fetch_weather", lambda: None)
        (tmp_path / "lantana.md").write_text(
            "# Lantana\n## Frequency History\n| Date | Change | Reason |\n|---|---|---|\n")
        a = mod.PlantAgent(db_path=tmp_path / "intel.db")
        plants = [{"name": "Lantana", "frequency_days": 7, "baseline_frequency_days": 7,
                   "location": "outdoor", "last_watered": "2026-05-31"}]
        a.context = {"plan": {"plants": plants}}
        output = json.dumps({
            "plants": [{"name": "Lantana", "status": "Stressed", "notes": ["wilting"],
                        "needs_photo": False,
                        "frequency_change": {"days": 3, "reason": "wilting"}}],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })
        a._apply_intelligence_output(output, plants)
        assert plants[0]["baseline_frequency_days"] == 5   # 7 -> 5 (step -2)
        assert plants[0]["frequency_days"] == 5            # no weather -> baseline
        assert "7→5 days" in (tmp_path / "lantana.md").read_text()


class TestIntelligenceFrontmatterCuration:
    """Task 5: _apply_intelligence_output regenerates frontmatter + curates Current Observations."""

    def test_refreshes_frontmatter_and_curates_observations(self, agent, tmp_path):
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)
        profile = plants_dir / "fern.md"
        profile.write_text(
            "# Fern\n\n## Current Observations\n- old stale note\n\n"
            "## Health Assessments\n<!-- Photo assessments appended here -->\n"
        )
        plant = {
            "name": "Fern", "frequency_days": 7, "baseline_frequency_days": 7,
            "last_watered": "2026-06-18", "location": "indoor",
            "sunlight": "indirect", "water_sensitivity": "medium",
            "needs_photo": False, "last_assessment": None,
        }
        output = json.dumps({
            "plants": [{"name": "Fern", "status": "Healthy",
                        "notes": ["vigorous growth", "no signs of stress"],
                        "needs_photo": False, "frequency_change": None}],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })
        with patch("agents.plant_agent.REPO_ROOT", tmp_path), \
                patch("agents.plant_profiles.PLANTS_DIR", plants_dir), \
                patch("agents.plant_profiles.PROFILES_DIR", plants_dir):
            agent._apply_intelligence_output(output, [plant])

        text = profile.read_text()
        assert text.startswith("---\n")
        assert "effective_frequency_days" in text
        assert "vigorous growth" in text
        assert "no signs of stress" in text
        assert "old stale note" not in text
        assert text.count("## Current Observations") == 1

    def test_no_notes_skips_section_rewrite(self, agent, tmp_path):
        """Empty notes list must not wipe out an existing Current Observations section."""
        plants_dir = tmp_path / "docs" / "plants"
        plants_dir.mkdir(parents=True)
        profile = plants_dir / "fern.md"
        profile.write_text("# Fern\n\n## Current Observations\n- existing note\n")
        plant = {
            "name": "Fern", "frequency_days": 7, "baseline_frequency_days": 7,
            "last_watered": "2026-06-18", "location": "indoor",
            "sunlight": "indirect", "water_sensitivity": "medium",
            "needs_photo": False, "last_assessment": None,
        }
        output = json.dumps({
            "plants": [{"name": "Fern", "status": "Healthy",
                        "notes": [], "needs_photo": False, "frequency_change": None}],
            "pruning": [], "tasks_created": [], "email_sent": False,
        })
        with patch("agents.plant_agent.REPO_ROOT", tmp_path), \
                patch("agents.plant_profiles.PLANTS_DIR", plants_dir), \
                patch("agents.plant_profiles.PROFILES_DIR", plants_dir):
            agent._apply_intelligence_output(output, [plant])

        assert "existing note" in profile.read_text()


def test_intelligence_prompt_documents_frequency_marker():
    import agents.plant_agent as mod
    text = (mod.REPO_ROOT / "agents" / "prompts" / "plant_intelligence.md").read_text()
    assert "frequency_change" in text
    assert "baseline" in text.lower()


# ---------------------------------------------------------------------------
# 6. _extract_json_array() — tolerant JSON parsing for LLM output
# ---------------------------------------------------------------------------

class TestExtractJsonArray:
    def test_plain_array(self):
        from agents.plant_agent import _extract_json_array
        assert _extract_json_array('[{"name": "Fern"}]') == [{"name": "Fern"}]

    def test_code_fenced(self):
        from agents.plant_agent import _extract_json_array
        raw = '```json\n[{"name": "Fern"}]\n```'
        assert _extract_json_array(raw) == [{"name": "Fern"}]

    def test_prose_wrapped(self):
        from agents.plant_agent import _extract_json_array
        raw = ('Here are the completed waterings:\n'
               '[{"name": "Fern", "completed_date": "2026-05-30"}]\n'
               'Let me know if you need anything else.')
        assert _extract_json_array(raw) == [
            {"name": "Fern", "completed_date": "2026-05-30"}]

    def test_empty_array(self):
        from agents.plant_agent import _extract_json_array
        assert _extract_json_array('[]') == []

    def test_unparseable_raises(self):
        from agents.plant_agent import _extract_json_array
        with pytest.raises(ValueError):
            _extract_json_array('I could not find any completed tasks.')


# ---------------------------------------------------------------------------
# 7. LLM-step failure propagation (no silent success-masking)
# ---------------------------------------------------------------------------

class TestStepFailurePropagation:
    """A synthesize() failure must propagate so BaseAgent marks the run
    partial_failure, and the gate must NOT be marked (so it retries)."""

    def test_send_status_email_propagates_and_leaves_gate_unmarked(self, agent):
        plant = _make_plant(name="Fern")
        agent.context["plan"] = {"plants": [plant], "weather_cache": {}}
        with patch.object(agent, "synthesize", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                agent._send_status_email()
        assert not agent.get_state("last_send_status_email")

    def test_intelligence_run_propagates_and_leaves_gate_unmarked(self, agent):
        plant = _make_plant(name="Fern")
        agent.context["plan"] = {"plants": [plant], "weather_cache": {}}
        with patch.object(agent, "synthesize", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                agent._intelligence_run()
        assert not agent.get_state("last_intelligence_run")
