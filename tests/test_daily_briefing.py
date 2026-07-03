"""Tests for DailyBriefingAgent.

Plant watering logic has been moved to PlantAgent — see test_plant_agent.py.
"""

from datetime import date, datetime, timedelta, timezone

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


class TestBuildPlantCareBlock:
    def _agent(self, tmp_path):
        agent = DailyBriefingAgent(db_path=tmp_path / "briefing.db")
        agent.run_id = agent.db.start_run(agent.name)
        agent.context = {}
        return agent

    def _plants(self, *overrides):
        today = date.today()
        base = {"name": "Testus", "frequency_days": 7, "last_watered": today.isoformat(), "location": "indoor"}
        return [{**base, **o} for o in overrides]

    def _seed(self, agent, plants):
        from agents.plant_model import Plant, PlantStore
        store = PlantStore(agent.db.db_path)
        for p in plants:
            data = {**p}
            data.setdefault("baseline_frequency_days", data.get("frequency_days", 7))
            store.add(Plant(**data))
        store.close()

    def test_empty_when_no_plants(self, tmp_path):
        agent = self._agent(tmp_path)
        assert agent._build_plant_care_block(date.today().isoformat()) == ""

    def test_overdue_plant_in_due_now(self, tmp_path):
        agent = self._agent(tmp_path)
        last = (date.today() - timedelta(days=10)).isoformat()
        self._seed(agent, [
            {"name": "Fern", "frequency_days": 7, "last_watered": last, "location": "indoor"}
        ])
        block = agent._build_plant_care_block(date.today().isoformat())
        assert "Water Fern" in block
        assert "overdue" in block
        assert "Coming up" not in block

    def test_due_today_plant_in_due_now(self, tmp_path):
        agent = self._agent(tmp_path)
        last = (date.today() - timedelta(days=7)).isoformat()
        self._seed(agent, [
            {"name": "Basil", "frequency_days": 7, "last_watered": last, "location": "indoor"}
        ])
        block = agent._build_plant_care_block(date.today().isoformat())
        assert "Water Basil" in block
        assert "due today" in block

    def test_upcoming_plant_in_coming_up(self, tmp_path):
        agent = self._agent(tmp_path)
        last = (date.today() - timedelta(days=4)).isoformat()
        self._seed(agent, [
            {"name": "Mint", "frequency_days": 7, "last_watered": last, "location": "indoor"}
        ])
        block = agent._build_plant_care_block(date.today().isoformat())
        assert "Water Mint" in block
        assert "Coming up" in block
        assert "overdue" not in block and "due today" not in block

    def test_plant_due_beyond_7_days_excluded(self, tmp_path):
        agent = self._agent(tmp_path)
        last = date.today().isoformat()
        self._seed(agent, [
            {"name": "Cactus", "frequency_days": 30, "last_watered": last, "location": "indoor"}
        ])
        block = agent._build_plant_care_block(date.today().isoformat())
        assert block == ""

    def test_pending_intelligence_action_in_due_now(self, tmp_path):
        agent = self._agent(tmp_path)
        agent.db.set_state("plant-agent", "pending_plant_actions", [
            {"plant": "Gazania", "action": "deadhead", "reason": "spent flowers", "date": date.today().isoformat()}
        ])
        block = agent._build_plant_care_block(date.today().isoformat())
        assert "Gazania" in block
        assert "Deadhead" in block
        assert "spent flowers" in block


class TestComingUpTodoistPrompt:
    """Guard against regressing the 'Coming Up' Todoist-task inclusion fix.

    Future-dated Todoist tasks were flakily missing from the Coming Up section
    because the fetch was buried and a blanket 'Inbox only' constraint dropped
    them. These assertions lock in the corrected prompt instructions.
    """

    @staticmethod
    def _prompt_text():
        import agents.daily_briefing as mod

        return (mod.REPO_ROOT / "agents" / "prompts" / "daily_briefing.md").read_text()

    def test_fetches_upcoming_todoist_tasks_for_coming_up(self):
        text = self._prompt_text()
        assert "find-tasks-by-date" in text
        assert "daysCount" in text and "30" in text
        # The fetch must be tied to the Coming Up section.
        assert "Coming Up" in text

    def test_inbox_only_restriction_excludes_coming_up(self):
        """The 'Inbox only' restriction must be explicitly scoped so it does
        NOT strip dated tasks out of the Coming Up section."""
        text = self._prompt_text().lower()
        # Some phrasing must clarify the Inbox-only rule does not apply to Coming Up.
        assert "does not apply to the coming up" in text or "not apply to coming up" in text
