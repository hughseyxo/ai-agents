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


class TestCalendarPromptMatchesServerSchema:
    """Regression test: calendar queries must use the param names the API
    actually accepts (timeMin/timeMax), not startTime/endTime. A mismatch here
    silently drops all time filtering — see 2026-08-14 bug."""

    def test_python_client_uses_timeMin_timeMax(self):
        """Calendar fetching lives in Python now (agents/calendar_client.py) —
        the prompt no longer issues MCP calendar calls at all."""
        import json

        from agents import calendar_client as cc

        seen = {}

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return json.dumps({"items": []}).encode()

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            return _Resp()

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(cc, "get_access_token", lambda: "t")
        monkeypatch.setattr(cc.urllib.request, "urlopen", fake_urlopen)
        try:
            cc.list_events("c", "2026-08-14T00:00:00Z", "2026-08-14T23:59:59Z")
        finally:
            monkeypatch.undo()

        assert "timeMin=" in seen["url"]
        assert "timeMax=" in seen["url"]
        assert "startTime=" not in seen["url"]
        assert "endTime=" not in seen["url"]

    def test_prompt_does_not_instruct_mcp_calendar_calls(self):
        agent = DailyBriefingAgent(db_path=":memory:")
        prompt = agent._build_prompt("2026-08-14")

        assert "gcal_list_events" not in prompt
        # The dead on-call id must not linger anywhere in the prompt.
        assert "8ubfqbcooeks9np5aufgu7g3mm0gj1rh" not in prompt

    def test_calendar_server_schema_expects_timeMin_timeMax(self):
        import importlib.util
        from pathlib import Path

        mcp_dir = Path(__file__).resolve().parent.parent / "mcp-servers"
        spec = importlib.util.spec_from_file_location("calendar_server", mcp_dir / "calendar_server.py")
        calendar_server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(calendar_server)

        gcal_tool = next(t for t in calendar_server.TOOLS if t["name"] == "gcal_list_events")
        properties = gcal_tool["inputSchema"]["properties"]

        assert "timeMin" in properties
        assert "timeMax" in properties
        assert "startTime" not in properties
        assert "endTime" not in properties


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


class TestBuildCalendarBlock:
    """Calendar data is fetched in Python and injected as a data block.

    Regression context (2026-08-15): the on-call calendar id hardcoded in the
    prompt had been deleted from the account and 404'd on every run, and the
    prompt told the model to filter out all-day events — between them the
    'Today' section showed nothing but plant tasks for weeks.
    """

    PRIMARY = "cianohughes@gmail.com"

    def _agent(self, tmp_path):
        agent = DailyBriefingAgent(db_path=tmp_path / "briefing.db")
        agent.run_id = agent.db.start_run(agent.name)
        agent.context = {}
        return agent

    @staticmethod
    def _timed(summary, day, start, end):
        return {
            "summary": summary,
            "start": {"dateTime": f"{day}T{start}:00+02:00"},
            "end": {"dateTime": f"{day}T{end}:00+02:00"},
        }

    @staticmethod
    def _all_day(summary, day, end_day=None):
        from datetime import date as _date, timedelta as _td

        end = end_day or (_date.fromisoformat(day) + _td(days=1)).isoformat()
        return {"summary": summary, "start": {"date": day}, "end": {"date": end}}

    def _stub(self, monkeypatch, by_calendar):
        """by_calendar: {calendar_id: [items] | Exception-ish error string}."""
        import agents.calendar_client as cc

        def fake_fetch(calendar_id, time_min, time_max, *a, **kw):
            entry = by_calendar.get(calendar_id, [])
            if isinstance(entry, str):
                return [], entry
            # Crude window filter so 'today' vs 'upcoming' calls differ.
            day_min, day_max = time_min[:10], time_max[:10]
            items = []
            for item in entry:
                start = item["start"]
                day = start.get("date") or start["dateTime"][:10]
                if day_min <= day <= day_max:
                    items.append(item)
            return items, None

        monkeypatch.setattr(cc, "fetch_events", fake_fetch)

    def test_empty_when_no_events(self, tmp_path, monkeypatch):
        self._stub(monkeypatch, {})
        agent = self._agent(tmp_path)
        assert agent._build_calendar_block("2026-08-15") == ""

    def test_all_day_event_appears_in_today(self, tmp_path, monkeypatch):
        self._stub(monkeypatch, {
            self.PRIMARY: [self._all_day("Alex Meade's birthday", "2026-08-15")],
        })
        agent = self._agent(tmp_path)
        block = agent._build_calendar_block("2026-08-15")

        assert "Today:" in block
        assert "Alex Meade's birthday" in block
        assert "all day" in block

    def test_oncall_shift_labelled_and_in_today(self, tmp_path, monkeypatch):
        from agents.calendar_client import ONCALL_CALENDAR_ID

        self._stub(monkeypatch, {
            ONCALL_CALENDAR_ID: [
                self._timed("File Storage Service: GMT_SRE", "2026-08-15", "12:30", "20:30"),
            ],
        })
        agent = self._agent(tmp_path)
        block = agent._build_calendar_block("2026-08-15")

        assert "[ON CALL]" in block
        assert "File Storage Service: GMT_SRE" in block
        assert "12:30" in block

    def test_upcoming_events_in_coming_up_section(self, tmp_path, monkeypatch):
        self._stub(monkeypatch, {
            self.PRIMARY: [
                self._timed("Michael Appointment", "2026-08-26", "17:15", "18:15"),
                self._all_day("Niamhs Ma over", "2026-08-18", "2026-08-27"),
            ],
        })
        agent = self._agent(tmp_path)
        block = agent._build_calendar_block("2026-08-15")

        assert "Coming up" in block
        assert "Michael Appointment" in block
        assert "Niamhs Ma over" in block
        # Multi-day all-day end is exclusive in the API — must render 26th, not 27th.
        assert "2026-08-27" not in block

    def test_events_beyond_30_days_excluded(self, tmp_path, monkeypatch):
        self._stub(monkeypatch, {
            self.PRIMARY: [self._all_day("Way off", "2026-10-20")],
        })
        agent = self._agent(tmp_path)
        assert agent._build_calendar_block("2026-08-15") == ""

    def test_short_timed_events_filtered(self, tmp_path, monkeypatch):
        self._stub(monkeypatch, {
            self.PRIMARY: [self._timed("Blink", "2026-08-15", "09:00", "09:05")],
        })
        agent = self._agent(tmp_path)
        assert agent._build_calendar_block("2026-08-15") == ""

    def test_short_oncall_events_not_filtered(self, tmp_path, monkeypatch):
        """On-call shifts are always shown, whatever their length."""
        from agents.calendar_client import ONCALL_CALENDAR_ID

        self._stub(monkeypatch, {
            ONCALL_CALENDAR_ID: [self._timed("GMT_SRE", "2026-08-15", "09:00", "09:05")],
        })
        agent = self._agent(tmp_path)
        assert "GMT_SRE" in agent._build_calendar_block("2026-08-15")

    def test_failed_calendar_surfaces_warning(self, tmp_path, monkeypatch):
        from agents.calendar_client import ONCALL_CALENDAR_ID

        self._stub(monkeypatch, {
            self.PRIMARY: [self._all_day("Birthday", "2026-08-15")],
            ONCALL_CALENDAR_ID: "HTTP 404",
        })
        agent = self._agent(tmp_path)
        block = agent._build_calendar_block("2026-08-15")

        assert "unavailable" in block
        assert "404" in block
        # The healthy calendar still renders.
        assert "Birthday" in block

    def test_all_calendars_failing_still_returns_warning_block(self, tmp_path, monkeypatch):
        from agents.calendar_client import ONCALL_CALENDAR_ID

        self._stub(monkeypatch, {
            self.PRIMARY: "HTTP 401",
            ONCALL_CALENDAR_ID: "HTTP 404",
        })
        agent = self._agent(tmp_path)
        block = agent._build_calendar_block("2026-08-15")

        assert "401" in block
        assert "404" in block

    def test_block_present_in_build_prompt(self, tmp_path, monkeypatch):
        import agents.daily_briefing as mod

        self._stub(monkeypatch, {
            self.PRIMARY: [self._all_day("Alex Meade's birthday", "2026-08-15")],
        })
        fake_prompt = tmp_path / "agents" / "prompts" / "daily_briefing.md"
        fake_prompt.parent.mkdir(parents=True)
        fake_prompt.write_text("# Briefing Prompt\n")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        agent = self._agent(tmp_path)
        assert "Alex Meade's birthday" in agent._build_prompt("2026-08-15")

    def test_fetch_failure_never_raises(self, tmp_path, monkeypatch):
        """A total calendar outage must not kill the briefing — it degrades to
        a visible warning instead."""
        import agents.calendar_client as cc

        def boom(*a, **kw):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(cc, "fetch_events", boom)
        agent = self._agent(tmp_path)
        block = agent._build_calendar_block("2026-08-15")

        assert "unavailable" in block
        assert "unexpected" in block


class TestCalendarToolsNotExposedToLLM:
    def test_calendar_mcp_not_in_allowed_tools(self):
        """Calendar is fetched in Python now — leaving the MCP server reachable
        would widen the prompt-injection surface for nothing."""
        assert not any("calendar" in t for t in DailyBriefingAgent.allowed_tools)
        assert "mcp__gmail__gmail_send" in DailyBriefingAgent.allowed_tools


class TestExtractHtml:
    """Saved output/daily-briefing-*.html must be openable in a browser.

    Both CLIs have wrapped the HTML in commentary or a ``` fence — a 2026-08-15
    run saved a `cat >> docs/agent-notes.md` bash fence as the whole file.
    """

    DOC = "<!DOCTYPE html>\n<html><body>hi</body></html>"

    def test_strips_preamble(self):
        out = DailyBriefingAgent._extract_html(f"I will now send the email.\n\n{self.DOC}")
        assert out == self.DOC

    def test_strips_code_fence_and_epilogue(self):
        raw = f"```html\n{self.DOC}\n```\n\nDone!"
        assert DailyBriefingAgent._extract_html(raw) == self.DOC

    def test_passthrough_when_already_clean(self):
        assert DailyBriefingAgent._extract_html(self.DOC) == self.DOC

    def test_falls_back_to_html_tag_without_doctype(self):
        doc = "<html><body>hi</body></html>"
        assert DailyBriefingAgent._extract_html(f"blah\n{doc}") == doc

    def test_returns_input_when_no_html_found(self):
        """A malformed response is preserved for debugging, not silently blanked."""
        assert DailyBriefingAgent._extract_html("total failure") == "total failure"
