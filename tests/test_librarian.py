import argparse
import pytest
from unittest.mock import patch
from agents.librarian import (
    LibrarianAgent,
    AGENT_NAMES,
    _write_learning_note,
    _coerce_conf,
    _parse_findings_json,
)


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
    agent.db.complete_run(r1, status="error", error="Antigravity CLI failed")
    r2 = agent.db.start_run("news-briefing")
    agent.db.complete_run(r2, status="error", error="Antigravity CLI failed")
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
    (output_dir / "daily-briefing-2026-05-22.html").write_text("<h1>Daily Briefing</h1>")
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
                                   "fix_type": "learnings", "learnings_entry": "Keep HTML under 50KB",
                                   "slug": "keep-html-under-50kb"}]
    (tmp_path / "docs" / "agent-learnings" / "news-briefing").mkdir(parents=True)
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        result = agent._apply_learnings()
    assert result["applied"] == 1
    notes = list((tmp_path / "docs" / "agent-learnings" / "news-briefing").glob("*.md"))
    assert len(notes) == 1
    assert "Keep HTML under 50KB" in notes[0].read_text()


def test_coerce_conf_handles_nonnumeric():
    # C5: LLM may emit a string/None confidence; must coerce, never raise.
    assert _coerce_conf(0.9) == 0.9
    assert _coerce_conf("high") == 0.0
    assert _coerce_conf(None) == 0.0
    assert _coerce_conf("0.85") == 0.85


def test_parse_findings_json_returns_empty_on_bad_input():
    # C4: a bad/non-list LLM response must yield [] (logged), not raise.
    assert _parse_findings_json("not json at all") == []
    assert _parse_findings_json('{"not": "a list"}') == []
    assert _parse_findings_json('[{"agent": "x"}]') == [{"agent": "x"}]


def test_write_learning_note_bounds_hostile_agent(tmp_path):
    # C3: a traversal-y agent name is sanitised to a bounded component, never escapes.
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        path = _write_learning_note("../../etc", "entry", 0.9, "slug", [], note_type="learnings")
    base = (tmp_path / "docs" / "agent-learnings").resolve()
    assert base in path.resolve().parents
    assert not (tmp_path / "etc").exists()  # nothing created outside the tree


def test_write_learning_note_rejects_empty_component(tmp_path):
    # C3: a name that sanitises to nothing must be refused, not silently joined.
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        with pytest.raises(ValueError):
            _write_learning_note("../..", "entry", 0.9, "slug", [], note_type="learnings")


def test_apply_learnings_hostile_agent_stays_bounded(tmp_path):
    # C3+C5: a finding with a path-traversal agent name writes only under the tree.
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "../../../tmp/evil", "confidence": 0.9,
                                  "fix_type": "learnings", "learnings_entry": "x",
                                  "slug": "x"}]
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        agent._apply_learnings()
    base = (tmp_path / "docs" / "agent-learnings").resolve()
    written = list((tmp_path / "docs" / "agent-learnings").glob("**/*.md"))
    assert written
    for p in written:
        assert base in p.resolve().parents
    assert not (tmp_path / "tmp" / "evil").exists()


def test_apply_learnings_nonnumeric_confidence_does_not_raise(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": "high",
                                  "fix_type": "learnings", "learnings_entry": "x", "slug": "x"}]
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        result = agent._apply_learnings()
    assert result["applied"] == 0


def test_apply_learnings_skips_below_threshold(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": 0.4,
                                   "fix_type": "learnings", "learnings_entry": "Some tip"}]
    (tmp_path / "docs" / "agent-learnings" / "news-briefing").mkdir(parents=True)
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        result = agent._apply_learnings()
    assert result["applied"] == 0


def test_apply_learnings_does_not_duplicate(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["findings"] = [{"agent": "news-briefing", "confidence": 0.9,
                                   "fix_type": "learnings", "learnings_entry": "Keep HTML under 50KB",
                                   "slug": "keep-html-under-50kb"}]
    with patch("agents.librarian.REPO_ROOT", tmp_path):
        agent._apply_learnings()
        agent._apply_learnings()  # same slug overwrites, not appends
    notes = list((tmp_path / "docs" / "agent-learnings" / "news-briefing").glob("*.md"))
    assert len(notes) == 1
    assert notes[0].read_text().count("Keep HTML under 50KB") == 1


def test_analyze_failures_calls_llm_when_failures_exist(tmp_path):
    agent = make_agent(tmp_path)
    agent.context["check_failures"] = {
        "failing_agents": ["news-briefing"],
        "error_details": {"news-briefing": ["Antigravity CLI failed"]},
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
