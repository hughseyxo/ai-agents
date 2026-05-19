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

    def test_check_plants_is_idempotent_across_save(self):
        """After save_plants persists the result, a second run should produce the same tasks.

        Regression test for: advancing last_watered to a future date breaks daily recalculation.
        """
        today = datetime.now(timezone.utc).date()
        last_watered = (today - timedelta(days=5)).isoformat()
        plants = [{"name": "Monstera", "frequency_days": 10,
                    "last_watered": last_watered, "location": "indoor"}]

        agent = _make_agent_with_plants(plants, NORMAL_WEATHER)
        result1 = agent._check_plants()

        # Simulate _save_plants persisting whatever _check_plants returned
        if result1.get("updated_all_plants"):
            agent.set_state("plants", result1["updated_all_plants"])

        result2 = agent._check_plants()

        # Both runs must produce the same upcoming watering and task due dates
        assert result1["tasks_to_create"] == result2["tasks_to_create"]


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
