"""Tests for the FloraPulse PWA gardening chat backend."""
import json
from unittest import mock

import plant_ui.chat_backend as cb


def _fake_run(stdout):
    return mock.Mock(returncode=0, stdout=stdout, stderr="")


def test_chat_builds_command_with_adddir_and_whitelist(monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _fake_run(json.dumps({"result": "hi", "session_id": "s1"}))
    monkeypatch.setattr(cb.subprocess, "run", fake_run)
    reply, sid = cb.chat("hello", scope="garden", plant_name=None, session_id=None)
    assert reply == "hi" and sid == "s1"
    cmd = seen["cmd"]
    assert "--add-dir" in cmd
    assert any("docs" in c for c in cmd)
    assert "--strict-mcp-config" in cmd
    assert "Read" in cmd and "Glob" in cmd
    assert "mcp__concierge__note_plant_observation" in cmd
    assert "mcp__concierge__save_plant_assessment" in cmd
    assert "--resume" not in cmd  # first turn


def test_chat_resumes_session(monkeypatch):
    seen = {}
    monkeypatch.setattr(cb.subprocess, "run",
                        lambda cmd, **kw: (seen.update(cmd=cmd) or _fake_run(json.dumps({"result": "ok", "session_id": "s2"}))))
    cb.chat("again", scope="plant", plant_name="Lavender", session_id="prev")
    assert "--resume" in seen["cmd"] and "prev" in seen["cmd"]


def test_chat_injects_plant_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(cb.plant_profiles, "read_profile_context", lambda n: "FROBNICATE_CTX")
    monkeypatch.setattr(cb.subprocess, "run",
                        lambda cmd, input="", **kw: (seen.update(inp=input) or _fake_run(json.dumps({"result": "ok"}))))
    cb.chat("q", scope="plant", plant_name="Lavender", session_id=None)
    assert "FROBNICATE_CTX" in seen["inp"]


def test_chat_failure_returns_none(monkeypatch):
    monkeypatch.setattr(cb.subprocess, "run", lambda cmd, **kw: mock.Mock(returncode=1, stdout="", stderr="boom"))
    reply, sid = cb.chat("hi", scope="garden", plant_name=None, session_id="x")
    assert reply is None and sid == "x"


def test_chat_disallows_tools_outside_whitelist(monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _fake_run(json.dumps({"result": "hi", "session_id": "s1"}))
    monkeypatch.setattr(cb.subprocess, "run", fake_run)
    cb.chat("hello", scope="garden", plant_name=None, session_id=None)
    cmd = seen["cmd"]
    assert "--disallowedTools" in cmd
    idx = cmd.index("--disallowedTools")
    disallowed = cmd[idx + 1:]
    # Every dangerous tool from the full concierge surface must be blocked.
    for dangerous in (
        "mcp__concierge__run_travel_agent",
        "mcp__concierge__save_recipe",
        "mcp__concierge__save_youtube_playlist",
        "mcp__concierge__remove_plant",
        "mcp__concierge__water_plants",
        "mcp__concierge__get_system_health",
        "mcp__concierge__get_agent_logs",
        "mcp__concierge__add_plant",
        "mcp__concierge__update_plant",
        "mcp__concierge__set_plant_frequency",
        "mcp__concierge__water_plant",
        "mcp__concierge__get_agent_status",
        "mcp__concierge__get_yopflix_status",
        "mcp__concierge__get_cron_schedule",
        "mcp__concierge__research_plant_watering",
        "mcp__concierge__research_plant_sunlight",
        "mcp__concierge__research_plant_water_sensitivity",
        "mcp__concierge__research_plant_traits",
        "mcp__concierge__get_travel_report",
        "mcp__concierge__suggest_free_time_tasks",
    ):
        assert dangerous in disallowed, f"{dangerous} must be in --disallowedTools"
    # None of the intended tools should be blocked.
    for allowed in cb._ALLOWED_TOOLS:
        if allowed.startswith("mcp__"):
            assert allowed not in disallowed, f"{allowed} should stay allowed"
