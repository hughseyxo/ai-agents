"""Tests for DailyBriefingAgent.

Plant watering logic has been moved to PlantAgent — see test_plant_agent.py.
"""

from datetime import datetime, timezone

import pytest

from agents.daily_briefing import DailyBriefingAgent


class TestDailyBriefingPlan:
    def test_plan_returns_today(self):
        agent = DailyBriefingAgent(db_path=":memory:")
        agent.run_id = agent.db.start_run(agent.name)
        agent.context = {}

        result = agent.plan()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert result["today"] == today

    def test_plan_no_previous_run(self):
        agent = DailyBriefingAgent(db_path=":memory:")
        # Do NOT start a run — testing the "never run before" state
        agent.run_id = None
        agent.context = {}

        result = agent.plan()

        assert result["last_run_date"] is None
        assert result["already_ran_today"] is False

    def test_steps_contains_briefing(self):
        agent = DailyBriefingAgent(db_path=":memory:")
        names = [s["name"] for s in agent.steps()]
        assert "briefing" in names
        assert len(names) == 1

    def test_steps_no_plant_steps(self):
        agent = DailyBriefingAgent(db_path=":memory:")
        names = [s["name"] for s in agent.steps()]
        assert "weather" not in names
        assert "sync_plant_completions" not in names
        assert "plants" not in names

    def test_report_includes_today(self):
        agent = DailyBriefingAgent(db_path=":memory:")
        agent.run_id = agent.db.start_run(agent.name)
        agent.context = {"plan": {"today": "2026-05-28"}}

        assert "2026-05-28" in agent.report()

    def test_build_prompt_includes_date(self, tmp_path, monkeypatch):
        """_build_prompt injects today's date into the prompt."""
        import agents.daily_briefing as mod

        # Point REPO_ROOT at tmp dir with a fake prompt file
        fake_prompt = tmp_path / "agents" / "prompts" / "daily_briefing.md"
        fake_prompt.parent.mkdir(parents=True)
        fake_prompt.write_text("# Briefing Prompt\n")

        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        agent = DailyBriefingAgent(db_path=":memory:")
        result = agent._build_prompt("2026-05-28")

        assert "2026-05-28" in result
        assert "# Briefing Prompt" in result
