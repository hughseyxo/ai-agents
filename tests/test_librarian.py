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


def test_collect_data_returns_all_agents(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["plan"] = {"mode": "audit", "today": "2026-05-22"}
    (tmp_path / "agents" / "prompts").mkdir(parents=True)
    (tmp_path / "output").mkdir(parents=True)
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        result = agent._collect_data()
    assert result["agents_analysed"] == len(AGENT_NAMES)
    assert "daily-briefing" in agent.context["collected"]["agent_stats"]


def test_collect_data_samples_output_files(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["plan"] = {"mode": "audit", "today": "2026-05-22"}
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "daily-briefing-2026-05-22.md").write_text("## Daily Briefing\nContent.")
    (tmp_path / "agents" / "prompts").mkdir(parents=True)
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        agent._collect_data()
    samples = agent.context["collected"]["output_samples"]
    assert "daily-briefing" in samples
    assert len(samples["daily-briefing"]) == 1


def test_collect_data_reads_existing_learnings(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["plan"] = {"mode": "audit", "today": "2026-05-22"}
    (tmp_path / "agents" / "prompts").mkdir(parents=True)
    (tmp_path / "output").mkdir(parents=True)
    ld = tmp_path / "docs" / "agent-learnings"
    ld.mkdir(parents=True)
    (ld / "news-briefing.md").write_text("- Keep HTML short\n")
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        agent._collect_data()
    assert agent.context["collected"]["learnings"]["news-briefing"] == "- Keep HTML short\n"


import json as _json


def test_analyze_parses_llm_findings(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["collected"] = {"agent_stats": {}, "output_samples": {}, "prompts": {}, "learnings": {}}
    findings = [{"agent": "news-briefing", "type": "reliability", "description": "Fails often",
                 "confidence": 0.9, "fix_type": "learnings", "suggested_fix": "Reduce size",
                 "learnings_entry": "- Keep HTML under 50KB"}]
    prompts_dir = tmp_path / "agents" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "librarian_audit.md").write_text("{{DATA}}")
    with patch.object(agent, "synthesize", return_value=_json.dumps(findings)):
        with patch("agents.librarian.REPO_ROOT", tmp_path):
            result = agent._analyze()
    assert result["findings"] == 1
    assert agent.context["findings"][0]["agent"] == "news-briefing"


def test_analyze_strips_markdown_fences(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["collected"] = {"agent_stats": {}, "output_samples": {}, "prompts": {}, "learnings": {}}
    findings = [{"agent": "news-briefing", "type": "quality", "description": "ok",
                 "confidence": 0.3, "fix_type": "report_only", "suggested_fix": "none"}]
    prompts_dir = tmp_path / "agents" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "librarian_audit.md").write_text("{{DATA}}")
    with patch.object(agent, "synthesize", return_value=f"```json\n{_json.dumps(findings)}\n```"):
        with patch("agents.librarian.REPO_ROOT", tmp_path):
            result = agent._analyze()
    assert result["findings"] == 1


def test_apply_learnings_writes_high_confidence_entry(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": 0.9,
                                   "fix_type": "learnings", "learnings_entry": "- Keep HTML under 50KB"}]
    (tmp_path / "docs" / "agent-learnings").mkdir(parents=True)
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        result = agent._apply_learnings()
    assert result["applied"] == 1
    lf = tmp_path / "docs" / "agent-learnings" / "news-briefing.md"
    assert "Keep HTML under 50KB" in lf.read_text()


def test_apply_learnings_skips_below_threshold(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": 0.4,
                                   "fix_type": "learnings", "learnings_entry": "- Some tip"}]
    (tmp_path / "docs" / "agent-learnings").mkdir(parents=True)
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        result = agent._apply_learnings()
    assert result["applied"] == 0


def test_apply_learnings_does_not_duplicate(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": 0.9,
                                   "fix_type": "learnings", "learnings_entry": "- Keep HTML under 50KB"}]
    ld = tmp_path / "docs" / "agent-learnings"
    ld.mkdir(parents=True)
    (ld / "news-briefing.md").write_text("- Keep HTML under 50KB\n")
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        agent._apply_learnings()
    assert (ld / "news-briefing.md").read_text().count("Keep HTML under 50KB") == 1


def test_analyze_failures_calls_llm_when_failures_exist(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["check_failures"] = {
        "failing_agents": ["news-briefing"],
        "error_details": {"news-briefing": ["Claude CLI failed"]},
    }
    findings = [{"agent": "news-briefing", "type": "reliability", "description": "CLI fails",
                 "confidence": 0.85, "fix_type": "learnings", "suggested_fix": "x",
                 "learnings_entry": "- Tip"}]
    prompts_dir = tmp_path / "agents" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "librarian_watch.md").write_text("{{DATA}}")
    with patch.object(agent, "synthesize", return_value=_json.dumps(findings)):
        with patch("agents.librarian.REPO_ROOT", tmp_path):
            result = agent._analyze_failures()
    assert result["findings"] == 1


def test_propose_changes_saves_medium_confidence_prompt_edit(tmp_path):
    agent = make_agent(tmp_path)
    prompt_file = tmp_path / "agents" / "prompts" / "news_briefing.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("# Original")
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": 0.65,
                                   "fix_type": "prompt_edit", "description": "Too verbose",
                                   "suggested_fix": "Shorten", "proposed_prompt_section": "# Shorter"}]
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        result = agent._propose_changes()
    assert result["proposals"] == 1
    files = list((tmp_path / "output" / "librarian" / "proposals").glob("*.json"))
    assert len(files) == 1
    p = _json.loads(files[0].read_text())
    assert p["original"] == "# Original"
    assert p["proposed"] == "# Shorter"
    assert p["status"] == "pending"


def test_propose_changes_skips_high_confidence(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": 0.9,
                                   "fix_type": "prompt_edit", "description": "x",
                                   "proposed_prompt_section": "y"}]
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        (tmp_path / "agents" / "prompts").mkdir(parents=True)
        result = agent._propose_changes()
    assert result["proposals"] == 0


def test_propose_changes_skips_report_only(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": 0.6,
                                   "fix_type": "report_only", "description": "x"}]
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        (tmp_path / "agents" / "prompts").mkdir(parents=True)
        result = agent._propose_changes()
    assert result["proposals"] == 0


def test_send_report_calls_synthesize(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["plan"] = {"mode": "audit", "today": "2026-05-22"}
    agent.context["findings"] = []
    agent.context["applied_learnings"] = []
    agent.context["proposals"] = []
    agent.context["collected"] = {"agent_stats": {}, "output_samples": {}, "prompts": {}, "learnings": {}}
    (tmp_path / "agents" / "prompts").mkdir(parents=True)
    (tmp_path / "agents" / "prompts" / "librarian_report.md").write_text("{{TODAY}}\n{{HTML_EMAIL}}")
    with patch.object(agent, "synthesize", return_value="sent") as mock_s:
        with patch("agents.librarian.REPO_ROOT", tmp_path):
            result = agent._send_report()
    assert result.get("sent") is True
    prompt = mock_s.call_args[0][0]
    assert "2026-05-22" in prompt
    assert "<!DOCTYPE html>" in prompt


def test_alert_calls_synthesize_when_failures_exist(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["plan"] = {"mode": "watch", "today": "2026-05-22"}
    agent.context["check_failures"] = {"failing_agents": ["news-briefing"],
                                        "error_details": {"news-briefing": ["CLI failed"]}}
    agent.context["findings"] = []
    agent.context["applied_learnings"] = []
    (tmp_path / "agents" / "prompts").mkdir(parents=True)
    (tmp_path / "agents" / "prompts" / "librarian_report.md").write_text("{{TODAY}}\n{{HTML_EMAIL}}")
    with patch.object(agent, "synthesize", return_value="sent") as mock_s:
        with patch("agents.librarian.REPO_ROOT", tmp_path):
            result = agent._alert()
    assert result.get("sent") is True
    assert mock_s.called
