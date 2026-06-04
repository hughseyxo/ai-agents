"""Tests for PlantAgent and plant_weather_cache DB methods.

Migrated from test_plant_weather_agent.py — PlantWeatherAgent was replaced by PlantAgent.
DB cache methods and weather adjustment logic are unchanged.
"""

from datetime import date, timedelta, datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from agents.db import AgentDB
from agents.plant_agent import PlantAgent


MILD_WEATHER = {
    "current": {"temp_c": 20, "humidity_pct": 55, "precip_mm": 0},
    "forecast": [
        {"date": "2026-05-25", "temp_max_c": 22, "precip_mm": 0},
        {"date": "2026-05-26", "temp_max_c": 23, "precip_mm": 0},
        {"date": "2026-05-27", "temp_max_c": 21, "precip_mm": 0},
    ],
    "recent_precip_mm": 0,
}

RAINY_WEATHER = {
    "current": {"temp_c": 17, "humidity_pct": 85, "precip_mm": 8},
    "forecast": [
        {"date": "2026-05-25", "temp_max_c": 18, "precip_mm": 14},
        {"date": "2026-05-26", "temp_max_c": 17, "precip_mm": 4},
        {"date": "2026-05-27", "temp_max_c": 19, "precip_mm": 0},
    ],
    "recent_precip_mm": 18,
}

SAMPLE_PLANTS = [
    {"name": "Monstera", "frequency_days": 7, "last_watered": "2026-05-17", "location": "indoor",
     "sunlight": "indirect", "water_sensitivity": "medium"},
    {"name": "Tomato", "frequency_days": 3, "last_watered": "2026-05-20", "location": "outdoor",
     "sunlight": "full sun", "water_sensitivity": "medium"},
]


@pytest.fixture
def db(tmp_path):
    return AgentDB(tmp_path / "test.db")


@pytest.fixture
def agent(tmp_path):
    a = PlantAgent(db_path=tmp_path / "test.db")
    a.run_id = a.db.start_run(a.name)
    a.context = {"plan": {"plants": [], "weather_cache": {}}}
    return a


# ---------------------------------------------------------------------------
# DB: plant_weather_cache methods (unchanged from original)
# ---------------------------------------------------------------------------

class TestPlantWeatherCache:
    def test_upsert_and_get(self, db):
        db.upsert_plant_weather_cache("Monstera", "2026-05-27", "2d later — heavy rain")
        cache = db.get_plant_weather_cache()
        assert len(cache) == 1
        assert cache[0]["plant_name"] == "Monstera"
        assert cache[0]["adjusted_date"] == "2026-05-27"
        assert cache[0]["adjustment_reason"] == "2d later — heavy rain"

    def test_upsert_overwrites_existing(self, db):
        db.upsert_plant_weather_cache("Monstera", "2026-05-27", "old reason")
        db.upsert_plant_weather_cache("Monstera", "2026-05-29", "new reason")
        cache = db.get_plant_weather_cache()
        assert len(cache) == 1
        assert cache[0]["adjusted_date"] == "2026-05-29"
        assert cache[0]["adjustment_reason"] == "new reason"

    def test_get_empty_returns_empty_list(self, db):
        assert db.get_plant_weather_cache() == []

    def test_multiple_plants_stored_separately(self, db):
        db.upsert_plant_weather_cache("Monstera", "2026-05-27", "")
        db.upsert_plant_weather_cache("Tomato", "2026-05-23", "hot & dry")
        cache = db.get_plant_weather_cache()
        assert len(cache) == 2
        names = {r["plant_name"] for r in cache}
        assert names == {"Monstera", "Tomato"}


# ---------------------------------------------------------------------------
# Agent: _weather_update (was _update_adjustments)
# ---------------------------------------------------------------------------

class TestWeatherUpdate:
    def test_stores_cache_for_each_plant(self, agent):
        agent.db.set_state("daily-briefing", "plants", SAMPLE_PLANTS)
        agent.context["plan"]["plants"] = SAMPLE_PLANTS
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            agent._weather_update()
        cache = agent.db.get_plant_weather_cache()
        names = {r["plant_name"] for r in cache}
        assert "Monstera" in names
        assert "Tomato" in names

    def test_returns_updated_count(self, agent):
        agent.context["plan"]["plants"] = SAMPLE_PLANTS
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            result = agent._weather_update()
        assert result["updated"] == 2

    def test_no_weather_skips_frequency_update(self, agent):
        # fetch_weather() returning None returns early, preserving existing frequency_days
        plants = [dict(p) for p in SAMPLE_PLANTS]
        agent.context["plan"]["plants"] = plants
        with patch("agents.plant_agent.fetch_weather", return_value=None):
            result = agent._weather_update()
        assert result.get("skipped") == "weather_unavailable"
        assert result["updated"] == 0
        assert plants[0]["baseline_frequency_days"] == plants[0]["frequency_days"]
        assert agent.db.get_plant_weather_cache() == []

    def test_handles_no_plants_gracefully(self, agent):
        agent.context["plan"]["plants"] = []
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            result = agent._weather_update()
        assert result["updated"] == 0

    def test_outdoor_plant_deferred_more_than_indoor_in_rain(self, agent):
        agent.context["plan"]["plants"] = SAMPLE_PLANTS
        with patch("agents.plant_agent.fetch_weather", return_value=RAINY_WEATHER):
            agent._weather_update()

        cache = {r["plant_name"]: r for r in agent.db.get_plant_weather_cache()}
        monstera_base = date(2026, 5, 17) + timedelta(days=7)   # 2026-05-24
        tomato_base = date(2026, 5, 20) + timedelta(days=3)     # 2026-05-23

        indoor_shift = (date.fromisoformat(cache["Monstera"]["adjusted_date"]) - monstera_base).days
        outdoor_shift = (date.fromisoformat(cache["Tomato"]["adjusted_date"]) - tomato_base).days

        assert outdoor_shift > indoor_shift


# ---------------------------------------------------------------------------
# Agent: _create_tasks — short-circuit and task creation
# ---------------------------------------------------------------------------

class TestCreateTasks:
    def _due_plant(self, name="Cilantro"):
        today = date.today()
        return {
            "name": name,
            "frequency_days": 7,
            "last_watered": (today - timedelta(days=7)).isoformat(),
            "location": "indoor",
            "sunlight": "indirect",
            "water_sensitivity": "medium",
        }

    def _future_plant(self):
        today = date.today()
        return {
            "name": "Succulent",
            "frequency_days": 14,
            "last_watered": today.isoformat(),
            "location": "indoor",
            "sunlight": "bright",
            "water_sensitivity": "low",
        }

    def test_short_circuits_when_nothing_due(self, agent):
        agent.context["plan"]["plants"] = [self._future_plant()]
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            with patch.object(agent, "synthesize") as mock_synth:
                result = agent._create_tasks()
        mock_synth.assert_not_called()
        assert result.get("skipped") is True

    def test_synthesize_called_for_due_plant(self, agent):
        agent.context["plan"]["plants"] = [self._due_plant()]
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            with patch.object(agent, "synthesize", return_value="created") as mock_synth:
                result = agent._create_tasks()
        mock_synth.assert_called_once()
        assert "Cilantro" in mock_synth.call_args[0][0]
        assert result["created"] == 1

    def test_dedup_prevents_second_call_same_day(self, agent):
        today = date.today()
        agent.db.mark_seen("plant-agent", "task_created", f"Cilantro:{today}")
        agent.context["plan"]["plants"] = [self._due_plant()]
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            with patch.object(agent, "synthesize", return_value="created") as mock_synth:
                result = agent._create_tasks()
        mock_synth.assert_not_called()
        assert result["skipped"] >= 1

    def test_marks_seen_after_creation(self, agent):
        today = date.today()
        agent.context["plan"]["plants"] = [self._due_plant()]
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            with patch.object(agent, "synthesize", return_value="created"):
                agent._create_tasks()
        assert agent.is_duplicate("task_created", f"Cilantro:{today}")

    def test_gate_blocks_when_recently_ran(self, agent):
        # Mark as ran just now — gate should block
        agent.set_state("last_create_tasks", datetime.now(timezone.utc).isoformat())
        agent.context["plan"]["plants"] = [self._due_plant()]
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            with patch.object(agent, "synthesize") as mock_synth:
                result = agent._create_tasks()
        mock_synth.assert_not_called()
        assert result.get("skipped") is True

    def test_returns_created_count_for_multiple_plants(self, agent):
        agent.context["plan"]["plants"] = [
            self._due_plant("Cilantro"),
            self._due_plant("Basil"),
        ]
        with patch("agents.plant_agent.fetch_weather", return_value=MILD_WEATHER):
            with patch.object(agent, "synthesize", return_value="created"):
                result = agent._create_tasks()
        assert result["created"] == 2


# ---------------------------------------------------------------------------
# Agent: schedule and name
# ---------------------------------------------------------------------------

def test_agent_has_hourly_schedule():
    assert PlantAgent.schedule == "0 * * * *"


def test_agent_name():
    assert PlantAgent.name == "plant-agent"
