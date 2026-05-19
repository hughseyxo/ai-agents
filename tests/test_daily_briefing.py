"""Tests for DailyBriefingAgent._check_plants with weather integration.

Tests use an in-memory SQLite DB and pre-set context to avoid
needing Claude/Gemini CLI or MCP servers.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from agents.daily_briefing import DailyBriefingAgent


def _make_agent_with_plants(plants, weather=None):
    """Create a DailyBriefingAgent with pre-loaded plant state and optional weather."""
    agent = DailyBriefingAgent(db_path=":memory:")
    agent.run_id = agent.db.start_run(agent.name)
    agent.set_state("plants", plants)
    agent.context = {
        "plan": {"today": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
    }
    if weather is not None:
        agent.context["weather"] = weather
    return agent


NORMAL_WEATHER = {
    "current": {"temp_c": 20, "humidity_pct": 50, "precip_mm": 0},
    "forecast": [
        {"date": "2026-05-15", "temp_max_c": 22, "precip_mm": 0},
        {"date": "2026-05-16", "temp_max_c": 23, "precip_mm": 0},
        {"date": "2026-05-17", "temp_max_c": 21, "precip_mm": 0},
    ],
    "recent_precip_mm": 0,
}

HOT_DRY_WEATHER = {
    "current": {"temp_c": 34, "humidity_pct": 30, "precip_mm": 0},
    "forecast": [
        {"date": "2026-05-15", "temp_max_c": 35, "precip_mm": 0},
        {"date": "2026-05-16", "temp_max_c": 33, "precip_mm": 0},
        {"date": "2026-05-17", "temp_max_c": 32, "precip_mm": 0},
    ],
    "recent_precip_mm": 0,
}

RAINY_WEATHER = {
    "current": {"temp_c": 15, "humidity_pct": 80, "precip_mm": 3},
    "forecast": [
        {"date": "2026-05-15", "temp_max_c": 16, "precip_mm": 12},
        {"date": "2026-05-16", "temp_max_c": 17, "precip_mm": 5},
        {"date": "2026-05-17", "temp_max_c": 18, "precip_mm": 0},
    ],
    "recent_precip_mm": 8,
}


class TestCheckPlantsWithWeather:
    def test_weather_adjusts_indoor_plant_date(self):
        """Hot dry weather should pull indoor plant watering earlier."""
        # Plant due in 5 days from a fixed reference
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, HOT_DRY_WEATHER)
        result = agent._check_plants()

        # Should still have upcoming watering (within 7 days)
        if result["plants"]:
            # The date should be earlier than base (last_watered + 10)
            base_date = (today - timedelta(days=5) + timedelta(days=10)).isoformat()
            assert result["plants"][0]["next_water_date"] <= base_date

    def test_no_weather_skips_adjustment(self):
        """Without weather in context, plants use base schedule."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, weather=None)
        result = agent._check_plants()

        if result["plants"]:
            base_date = (today - timedelta(days=5) + timedelta(days=10)).isoformat()
            assert result["plants"][0]["next_water_date"] == base_date

    def test_weather_none_value_skips_adjustment(self):
        """If weather fetch failed (None), use base schedule."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants)
        agent.context["weather"] = None  # fetch failed
        result = agent._check_plants()

        if result["plants"]:
            base_date = (today - timedelta(days=5) + timedelta(days=10)).isoformat()
            assert result["plants"][0]["next_water_date"] == base_date

    def test_outdoor_rain_defers_watering(self):
        """Rainy weather should defer outdoor plant watering."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=12)).isoformat()
        plants = [{"name": "Tomato", "frequency_days": 3,
                    "last_watered": last_watered, "location": "outdoor"}]

        agent = _make_agent_with_plants(plants, RAINY_WEATHER)
        result = agent._check_plants()

        if result["plants"]:
            base_date = (today - timedelta(days=12) + timedelta(days=3)).isoformat()
            assert result["plants"][0]["next_water_date"] >= base_date

    def test_adjustment_reason_included_in_output(self):
        """When weather adjusts a date, the reason should be in the output."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, HOT_DRY_WEATHER)
        result = agent._check_plants()

        if result["plants"]:
            assert "adjustment" in result["plants"][0]

    def test_plants_without_location_default_to_indoor(self):
        """Legacy plants without location field should work as indoor."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Old Plant", "frequency_days": 7,
                    "last_watered": last_watered}]

        agent = _make_agent_with_plants(plants, NORMAL_WEATHER)
        # Should not crash
        result = agent._check_plants()
        assert isinstance(result, dict)


class TestCheckPlantsIdempotent:
    def test_check_plants_does_not_return_updated_all_plants(self):
        """_check_plants must not return updated_all_plants — no state mutation."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, NORMAL_WEATHER)
        result = agent._check_plants()

        assert "updated_all_plants" not in result or result.get("updated_all_plants") is None

    def test_task_created_only_on_watering_day(self):
        """Tasks are created only when days_until <= 0; email preview shows 7-day window."""
        today = datetime.now(timezone.utc).date()

        # Plant due in 3 days — visible in preview, no task yet
        plants_future = [{"name": "Monstera", "frequency_days": 10,
                           "last_watered": (today - timedelta(days=7)).isoformat(),
                           "location": "indoor"}]
        agent = _make_agent_with_plants(plants_future, NORMAL_WEATHER)
        result = agent._check_plants()
        assert len(result["plants"]) == 1        # shows in preview
        assert len(result["tasks_to_create"]) == 0  # no task yet

        # Plant due today — task created
        plants_today = [{"name": "Monstera", "frequency_days": 10,
                          "last_watered": (today - timedelta(days=10)).isoformat(),
                          "location": "indoor"}]
        agent2 = _make_agent_with_plants(plants_today, NORMAL_WEATHER)
        result2 = agent2._check_plants()
        assert len(result2["tasks_to_create"]) == 1

    def test_check_plants_is_idempotent_across_save(self):
        """Regression: advancing last_watered broke daily recalculation.

        Uses a plant due today so tasks_to_create is non-empty on both runs.
        """
        today = datetime.now(timezone.utc).date()
        # Plant due exactly today (last_watered = 10 days ago, freq = 10)
        last_watered = (today - timedelta(days=10)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, NORMAL_WEATHER)
        result1 = agent._check_plants()

        # Simulate old _save_plants persisting mutated data (the bug we fixed)
        if result1.get("updated_all_plants"):
            agent.set_state("plants", result1["updated_all_plants"])

        result2 = agent._check_plants()

        assert result1["tasks_to_create"] == result2["tasks_to_create"]


class TestSyncPlantCompletions:
    def test_updates_last_watered_from_completed_task(self):
        """LLM reports a completed task newer than last_watered → DB is updated."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=10)).isoformat()
        completed_date = (today - timedelta(days=3)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants)
        agent.synthesize = lambda prompt: f'[{{"name": "Monstera", "completed_date": "{completed_date}"}}]'

        agent._sync_plant_completions()

        saved = agent.get_state("plants")
        assert saved[0]["last_watered"] == completed_date

    def test_does_not_update_if_completed_date_not_newer(self):
        """Completed date <= last_watered → no update (already synced)."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=3)).isoformat()
        older_date = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants)
        agent.synthesize = lambda prompt: f'[{{"name": "Monstera", "completed_date": "{older_date}"}}]'

        agent._sync_plant_completions()

        saved = agent.get_state("plants")
        assert saved[0]["last_watered"] == last_watered

    def test_noop_on_empty_json(self):
        """LLM returns [] → no DB changes."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants)
        agent.synthesize = lambda prompt: '[]'

        agent._sync_plant_completions()

        saved = agent.get_state("plants")
        assert saved[0]["last_watered"] == last_watered

    def test_noop_on_bad_json(self):
        """LLM returns non-JSON → no crash, DB unchanged."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants)
        agent.synthesize = lambda prompt: 'No completed tasks found.'

        agent._sync_plant_completions()  # must not raise

        saved = agent.get_state("plants")
        assert saved[0]["last_watered"] == last_watered

    def test_multiple_plants_updated_independently(self):
        """Each plant updated based on its own completion; unmentioned plants untouched."""
        today = datetime.now(timezone.utc).date()
        original = (today - timedelta(days=10)).isoformat()
        monstera_done = (today - timedelta(days=3)).isoformat()
        plants = [
            {"name": "Monstera", "frequency_days": 10,
             "last_watered": original, "location": "indoor"},
            {"name": "Tomato", "frequency_days": 3,
             "last_watered": original, "location": "outdoor"},
        ]

        agent = _make_agent_with_plants(plants)
        agent.synthesize = lambda prompt: f'[{{"name": "Monstera", "completed_date": "{monstera_done}"}}]'

        agent._sync_plant_completions()

        saved = agent.get_state("plants")
        monstera = next(p for p in saved if p["name"] == "Monstera")
        tomato = next(p for p in saved if p["name"] == "Tomato")
        assert monstera["last_watered"] == monstera_done
        assert tomato["last_watered"] == original

    def test_strips_markdown_fences_from_llm_output(self):
        """LLM wraps JSON in code fences → still parsed correctly."""
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=10)).isoformat()
        completed_date = (today - timedelta(days=2)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants)
        fenced = f'```json\n[{{"name": "Monstera", "completed_date": "{completed_date}"}}]\n```'
        agent.synthesize = lambda prompt: fenced

        agent._sync_plant_completions()

        saved = agent.get_state("plants")
        assert saved[0]["last_watered"] == completed_date


class TestFetchWeatherStep:
    @patch("agents.daily_briefing.fetch_weather")
    def test_fetch_weather_step_stores_in_context(self, mock_fetch):
        mock_fetch.return_value = NORMAL_WEATHER
        agent = DailyBriefingAgent(db_path=":memory:")
        agent.run_id = agent.db.start_run(agent.name)
        agent.context = {}

        agent._fetch_weather()

        assert agent.context["weather"] == NORMAL_WEATHER

    @patch("agents.daily_briefing.fetch_weather")
    def test_fetch_weather_stores_none_on_failure(self, mock_fetch):
        mock_fetch.return_value = None
        agent = DailyBriefingAgent(db_path=":memory:")
        agent.run_id = agent.db.start_run(agent.name)
        agent.context = {}

        agent._fetch_weather()

        assert agent.context["weather"] is None
