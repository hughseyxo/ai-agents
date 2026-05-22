import argparse
import pytest
from unittest.mock import patch
from agents.librarian import LibrarianAgent, AGENT_NAMES


def make_agent(tmp_path):
    return LibrarianAgent(db_path=tmp_path / "test.db")


def fake_args(mode):
    return argparse.Namespace(mode=mode)


def test_configure_rejects_invalid_mode(tmp_path):
    agent = make_agent(tmp_path)
    with pytest.raises(ValueError, match="audit.*watch"):
        agent.configure(fake_args("search"))


def test_configure_accepts_audit(tmp_path):
    agent = make_agent(tmp_path)
    agent.configure(fake_args("audit"))
    assert agent.mode == "audit"


def test_configure_accepts_watch(tmp_path):
    agent = make_agent(tmp_path)
    agent.configure(fake_args("watch"))
    assert agent.mode == "watch"


def test_audit_steps(tmp_path):
    agent = make_agent(tmp_path)
    agent.mode = "audit"
    names = [s["name"] for s in agent.steps()]
    assert names == ["collect_data", "analyze", "apply_learnings", "propose_changes", "send_report"]


def test_watch_steps(tmp_path):
    agent = make_agent(tmp_path)
    agent.mode = "watch"
    names = [s["name"] for s in agent.steps()]
    assert names == ["check_failures", "analyze_failures", "apply_learnings", "alert"]


def test_send_report_has_side_effects(tmp_path):
    agent = make_agent(tmp_path)
    agent.mode = "audit"
    step = next(s for s in agent.steps() if s["name"] == "send_report")
    assert step.get("side_effects") is True


def test_alert_has_side_effects(tmp_path):
    agent = make_agent(tmp_path)
    agent.mode = "watch"
    step = next(s for s in agent.steps() if s["name"] == "alert")
    assert step.get("side_effects") is True


def test_librarian_registered_in_runner():
    from agents.runner import AGENT_REGISTRY
    assert "librarian" in AGENT_REGISTRY


def test_check_failures_empty_when_no_runs(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["plan"] = {"mode": "watch", "today": "2026-05-22"}
    result = agent._check_failures()
    assert result["failing_agents"] == []


def test_check_failures_detects_two_consecutive_errors(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["plan"] = {"mode": "watch", "today": "2026-05-22"}
    r1 = agent.db.start_run("news-briefing")
    agent.db.complete_run(r1, status="error", error="Claude CLI failed")
    r2 = agent.db.start_run("news-briefing")
    agent.db.complete_run(r2, status="error", error="Claude CLI failed")
    result = agent._check_failures()
    assert "news-briefing" in result["failing_agents"]


def test_check_failures_ignores_single_failure_followed_by_success(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["plan"] = {"mode": "watch", "today": "2026-05-22"}
    r1 = agent.db.start_run("news-briefing")
    agent.db.complete_run(r1, status="error", error="fail")
    r2 = agent.db.start_run("news-briefing")
    agent.db.complete_run(r2, status="success")
    result = agent._check_failures()
    assert "news-briefing" not in result["failing_agents"]


def test_analyze_failures_skips_with_no_failing_agents(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["check_failures"] = {"failing_agents": [], "error_details": {}}
    result = agent._analyze_failures()
    assert result.get("skipped") is True


def test_alert_skips_with_no_failing_agents(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["check_failures"] = {"failing_agents": [], "error_details": {}}
    result = agent._alert()
    assert result.get("skipped") is True
