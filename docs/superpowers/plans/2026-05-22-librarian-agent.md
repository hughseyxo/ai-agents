# Librarian Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a LibrarianAgent that audits other agents weekly for reliability and quality issues, auto-applies high-confidence fixes as learnings files, and emails medium-confidence prompt changes with one-click approve/reject.

**Architecture:** Two run modes (audit/watch) in one agent class. Python gathers all data; a single LLM call produces structured findings with confidence scores (≥0.8 → auto-apply learnings, 0.5–0.79 → email proposal, <0.5 → report only). Learnings files are prepended to every future `synthesize()` call for that agent via a 5-line BaseAgent hook. Approve/reject are GET routes added to the existing bridge server.

**Tech Stack:** Python 3.10, SQLite (existing agents.db), Gmail MCP (email), BaseAgent pattern, existing bridge_server.py HTTP server, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agents/base.py` | Modify | Prepend `docs/agent-learnings/<name>.md` to `synthesize()` if present |
| `agents/librarian.py` | Create | LibrarianAgent — both modes, all steps |
| `agents/prompts/librarian_audit.md` | Create | Weekly analysis prompt |
| `agents/prompts/librarian_watch.md` | Create | Daily failure-scan prompt |
| `agents/prompts/librarian_report.md` | Create | Email send prompt (same pattern as news_briefing) |
| `agents/runner.py` | Modify | Add `librarian` to AGENT_REGISTRY; extend `--mode` choices |
| `mcp-servers/bridge_server.py` | Modify | Add `do_GET` + approve/reject handlers |
| `tests/test_synthesize.py` | Modify | 2 new tests for learnings injection |
| `tests/test_librarian.py` | Create | All librarian tests |
| `.gitignore` | Modify | Ignore `docs/agent-learnings/` and `output/librarian/` |
| `CLAUDE.md` | Modify | Document new agent + prompts |

---

### Task 1: BaseAgent learnings injection

**Files:**
- Modify: `agents/base.py` — `synthesize()` method
- Modify: `tests/test_synthesize.py`

- [ ] Add two failing tests to `tests/test_synthesize.py`:

```python
@patch("agents.base.subprocess.run")
def test_learnings_file_prepended_to_prompt(mock_run, tmp_path):
    mock_run.return_value = mock_result(stdout="output")
    agent = make_agent()
    learnings_dir = tmp_path / "docs" / "agent-learnings"
    learnings_dir.mkdir(parents=True)
    (learnings_dir / f"{agent.name}.md").write_text("- Keep responses short\n")
    with patch("agents.base.REPO_ROOT", tmp_path):
        agent.synthesize("Do a thing")
    cmd = mock_run.call_args[0][0]
    prompt = cmd[cmd.index("-p") + 1]
    assert "Agent Learnings" in prompt
    assert "Keep responses short" in prompt
    assert "Do a thing" in prompt

@patch("agents.base.subprocess.run")
def test_no_learnings_file_leaves_prompt_unchanged(mock_run, tmp_path):
    mock_run.return_value = mock_result(stdout="output")
    agent = make_agent()
    with patch("agents.base.REPO_ROOT", tmp_path):
        agent.synthesize("Do a thing")
    cmd = mock_run.call_args[0][0]
    prompt = cmd[cmd.index("-p") + 1]
    assert "Agent Learnings" not in prompt
    assert prompt == "Do a thing"
```

- [ ] Run to confirm failures:
```
pytest tests/test_synthesize.py::test_learnings_file_prepended_to_prompt tests/test_synthesize.py::test_no_learnings_file_leaves_prompt_unchanged -v
```
Expected: FAIL (feature not implemented)

- [ ] Add learnings injection at the top of `synthesize()` in `agents/base.py`, immediately before `last_error = None`:

```python
def synthesize(self, prompt: str) -> str:
    learnings_path = REPO_ROOT / "docs" / "agent-learnings" / f"{self.name}.md"
    if learnings_path.exists():
        learnings = learnings_path.read_text().strip()
        if learnings:
            prompt = f"## Agent Learnings (apply these)\n{learnings}\n\n---\n\n{prompt}"
    last_error = None
    for provider in (self.providers or self.PROVIDERS):
        # ... rest unchanged
```

- [ ] Run all synthesize tests:
```
pytest tests/test_synthesize.py -v
```
Expected: all pass

- [ ] Commit:
```bash
git add agents/base.py tests/test_synthesize.py
git commit -m "feat: prepend agent learnings to synthesize() when docs/agent-learnings/<name>.md exists"
```

---

### Task 2: LibrarianAgent skeleton

**Files:**
- Create: `agents/librarian.py`
- Create: `tests/test_librarian.py`

- [ ] Create `tests/test_librarian.py` with skeleton tests:

```python
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
```

- [ ] Run to confirm failures:
```
pytest tests/test_librarian.py -v
```
Expected: ImportError (module doesn't exist)

- [ ] Create `agents/librarian.py`:

```python
"""Librarian Agent — audits and improves other agents.

Cron (manual — uses --mode args not supported by install-cron):
  0 6 * * 0    run-agent.sh librarian --mode audit    # Sunday full audit
  0 6 * * 1-6  run-agent.sh librarian --mode watch   # Mon-Sat failure check
"""
import json
import re
import uuid
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import BaseAgent, REPO_ROOT

AGENT_NAMES = ["daily-briefing", "news-briefing", "security-audit"]

OUTPUT_PATTERNS = {
    "daily-briefing": "daily-briefing-*.md",
    "news-briefing": "daily-news-briefing-*.md",
    "security-audit": "security-audit-*.md",
}

PROMPT_STEMS = {
    "daily-briefing": "daily_briefing",
    "news-briefing": "news_briefing",
}

BRIDGE_BASE = "http://yopflix.tailed77a8.ts.net:4242"


class LibrarianAgent(BaseAgent):
    name = "librarian"
    schedule = ""
    model = "claude-haiku-4-5"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode: str | None = None

    def configure(self, args):
        mode = getattr(args, "mode", None)
        if mode not in ("audit", "watch"):
            raise ValueError(f"librarian requires --mode audit or --mode watch, got: {mode!r}")
        self.mode = mode

    def plan(self) -> dict:
        return {"mode": self.mode, "today": datetime.now(timezone.utc).strftime("%Y-%m-%d")}

    def steps(self) -> list[dict]:
        if self.mode == "audit":
            return [
                {"name": "collect_data",    "fn": self._collect_data},
                {"name": "analyze",         "fn": self._analyze},
                {"name": "apply_learnings", "fn": self._apply_learnings},
                {"name": "propose_changes", "fn": self._propose_changes},
                {"name": "send_report",     "fn": self._send_report, "side_effects": True},
            ]
        return [
            {"name": "check_failures",   "fn": self._check_failures},
            {"name": "analyze_failures", "fn": self._analyze_failures},
            {"name": "apply_learnings",  "fn": self._apply_learnings},
            {"name": "alert",            "fn": self._alert, "side_effects": True},
        ]

    def report(self) -> str:
        today = self.context["plan"]["today"]
        mode = self.context["plan"]["mode"]
        return f"Librarian {mode} for {today} complete"

    def _collect_data(self): raise NotImplementedError
    def _analyze(self): raise NotImplementedError
    def _apply_learnings(self): raise NotImplementedError
    def _propose_changes(self): raise NotImplementedError
    def _send_report(self): raise NotImplementedError
    def _check_failures(self): raise NotImplementedError
    def _analyze_failures(self): raise NotImplementedError
    def _alert(self): raise NotImplementedError
```

- [ ] Update `agents/runner.py` — add librarian to `AGENT_REGISTRY` and extend `--mode` choices:

In `AGENT_REGISTRY` dict:
```python
"librarian": "agents.librarian:LibrarianAgent",
```

Change `--mode` argument:
```python
run_parser.add_argument(
    "--mode",
    choices=["search", "plan", "audit", "watch"],
    default="search",
    help="Mode: travel-agent uses search/plan; librarian uses audit/watch",
)
```

- [ ] Run tests:
```
pytest tests/test_librarian.py -v
```
Expected: all pass except `test_librarian_registered_in_runner` (just added runner change, should now pass too)

- [ ] Commit:
```bash
git add agents/librarian.py agents/runner.py tests/test_librarian.py
git commit -m "feat: add LibrarianAgent skeleton and register in runner"
```

---

### Task 3: watch mode — check_failures

**Files:**
- Modify: `agents/librarian.py`
- Modify: `tests/test_librarian.py`

- [ ] Add failing tests to `tests/test_librarian.py`:

```python
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
```

- [ ] Run to confirm failures:
```
pytest tests/test_librarian.py::test_check_failures_empty_when_no_runs -v
```
Expected: FAIL (NotImplementedError)

- [ ] Implement `_check_failures`, `_analyze_failures` (skip guard only), `_alert` (skip guard only) in `agents/librarian.py`:

```python
def _check_failures(self) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()
    failing = []
    error_details: dict[str, list[str]] = {}

    for agent_name in AGENT_NAMES:
        runs = self.db.get_run_history(agent_name, limit=5)
        recent = [r for r in runs if (r.get("finished_at") or r.get("started_at", "")) >= cutoff]
        if len(recent) < 2:
            continue
        consecutive = 0
        for r in recent:
            if r["status"] in ("error", "partial_failure"):
                consecutive += 1
            else:
                break
        if consecutive >= 2:
            failing.append(agent_name)
            error_details[agent_name] = [
                r.get("error") or "" for r in recent
                if r["status"] in ("error", "partial_failure")
            ][:3]

    return {"failing_agents": failing, "error_details": error_details}


def _analyze_failures(self) -> dict:
    check = self.context.get("check_failures") or {}
    if not check.get("failing_agents"):
        return {"skipped": True}
    raise NotImplementedError  # implemented in Task 5


def _alert(self) -> dict:
    check = self.context.get("check_failures") or {}
    if not check.get("failing_agents"):
        return {"skipped": True, "reason": "no_failures"}
    raise NotImplementedError  # implemented in Task 7
```

- [ ] Run tests:
```
pytest tests/test_librarian.py -v
```
Expected: all new tests pass; NotImplementedError tests not yet added

- [ ] Commit:
```bash
git add agents/librarian.py tests/test_librarian.py
git commit -m "feat: implement check_failures with 2-consecutive-failure detection"
```

---

### Task 4: collect_data (audit mode)

**Files:**
- Modify: `agents/librarian.py`
- Modify: `tests/test_librarian.py`

- [ ] Add failing tests:

```python
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
```

- [ ] Run to confirm failures:
```
pytest tests/test_librarian.py::test_collect_data_returns_all_agents -v
```

- [ ] Implement `_collect_data()` in `agents/librarian.py`:

```python
def _collect_data(self) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=30)).isoformat()

    agent_stats: dict = {}
    for agent_name in AGENT_NAMES:
        runs = self.db.get_run_history(agent_name, limit=40)
        recent = [r for r in runs if (r.get("started_at") or "") >= cutoff]
        failures = [r for r in recent if r["status"] in ("error", "partial_failure")]
        consecutive = 0
        for r in runs[:10]:
            if r["status"] in ("error", "partial_failure"):
                consecutive += 1
            else:
                break
        step_errors: dict[str, list[str]] = {}
        for r in failures[:5]:
            for s in self.db.get_step_results(r["id"]):
                if s["status"] == "error":
                    step_errors.setdefault(s["step"], []).append(s.get("error") or "")
        agent_stats[agent_name] = {
            "total_runs": len(recent),
            "failures": len(failures),
            "failure_rate": round(len(failures) / max(len(recent), 1), 2),
            "consecutive_failures": consecutive,
            "step_errors": step_errors,
        }

    output_dir = REPO_ROOT / "output"
    output_samples: dict[str, list[str]] = {}
    for name, pattern in OUTPUT_PATTERNS.items():
        files = sorted(output_dir.glob(pattern), reverse=True)[:5] if output_dir.exists() else []
        output_samples[name] = [f.read_text()[:2000] for f in files]

    prompts: dict[str, str] = {}
    prompts_dir = REPO_ROOT / "agents" / "prompts"
    if prompts_dir.exists():
        for f in prompts_dir.glob("*.md"):
            if not f.name.startswith("librarian"):
                prompts[f.stem] = f.read_text()[:3000]

    learnings: dict[str, str] = {}
    ld = REPO_ROOT / "docs" / "agent-learnings"
    if ld.exists():
        for f in ld.glob("*.md"):
            learnings[f.stem] = f.read_text()

    self.context["collected"] = {
        "agent_stats": agent_stats,
        "output_samples": output_samples,
        "prompts": prompts,
        "learnings": learnings,
    }
    return {"agents_analysed": len(agent_stats)}
```

- [ ] Run tests:
```
pytest tests/test_librarian.py -v
```
Expected: all pass

- [ ] Commit:
```bash
git add agents/librarian.py tests/test_librarian.py
git commit -m "feat: implement collect_data for audit mode"
```

---

### Task 5: LLM analysis + apply_learnings + prompts

**Files:**
- Create: `agents/prompts/librarian_audit.md`
- Create: `agents/prompts/librarian_watch.md`
- Modify: `agents/librarian.py`
- Modify: `tests/test_librarian.py`

- [ ] Create `agents/prompts/librarian_audit.md`:

```markdown
You are a librarian agent reviewing the performance and output quality of automated agents.

Analyse the structured data below. Return ONLY a JSON array of findings. Each finding:
- "agent": agent name (e.g. "news-briefing")
- "type": "reliability" or "quality"
- "description": concise observation (1-2 sentences)
- "confidence": 0.0-1.0 (certainty this fix is correct and will help)
- "fix_type": "learnings" | "prompt_edit" | "report_only"
- "suggested_fix": plain English description
- "learnings_entry": (required if fix_type="learnings") one bullet point, e.g. "- Keep HTML under 50KB"
- "proposed_prompt_section": (required if fix_type="prompt_edit") full replacement text

Confidence guidance — be conservative:
- ≥0.8: only for patterns seen across multiple runs with a clear, low-risk fix
- 0.5-0.79: plausible improvement but not certain
- <0.5: observation only

Data:
{{DATA}}
```

- [ ] Create `agents/prompts/librarian_watch.md`:

```markdown
One or more agents have logged 2+ consecutive failures. Analyse the errors below.

Return ONLY a JSON array with one finding per failing agent:
- "agent": agent name
- "type": "reliability"
- "description": error pattern (1 sentence)
- "confidence": 0.0-1.0
- "fix_type": "learnings" | "report_only"
- "suggested_fix": plain English
- "learnings_entry": (required if fix_type="learnings") one bullet point

Do NOT suggest prompt_edit — that requires full audit context.

Data:
{{DATA}}
```

- [ ] Add failing tests to `tests/test_librarian.py`:

```python
import json as _json
from unittest.mock import patch, MagicMock

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
```

- [ ] Run to confirm failures:
```
pytest tests/test_librarian.py::test_analyze_parses_llm_findings tests/test_librarian.py::test_apply_learnings_writes_high_confidence_entry -v
```

- [ ] Implement `_analyze()`, `_apply_learnings()`, and the real `_analyze_failures()` in `agents/librarian.py`:

```python
def _analyze(self) -> dict:
    collected = self.context.get("collected", {})
    prompt_path = REPO_ROOT / "agents" / "prompts" / "librarian_audit.md"
    prompt = prompt_path.read_text().replace("{{DATA}}", json.dumps(collected))
    text = self.synthesize(prompt).strip()
    if text.startswith("```"):
        text = re.sub(r'^```[a-z]*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text).strip()
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        text = match.group(0)
    findings = json.loads(text)
    self.context["findings"] = findings
    return {"findings": len(findings)}


def _apply_learnings(self) -> dict:
    findings = self.context.get("findings") or []
    ld = REPO_ROOT / "docs" / "agent-learnings"
    ld.mkdir(parents=True, exist_ok=True)
    applied = []
    for f in findings:
        if (f.get("confidence", 0) >= 0.8
                and f.get("fix_type") == "learnings"
                and f.get("learnings_entry")):
            lf = ld / f"{f['agent']}.md"
            existing = lf.read_text().strip() if lf.exists() else ""
            entry = f["learnings_entry"].strip()
            if entry not in existing:
                lf.write_text((existing + "\n" + entry).strip() + "\n")
                applied.append({"agent": f["agent"], "entry": entry})
    self.context["applied_learnings"] = applied
    return {"applied": len(applied)}


def _analyze_failures(self) -> dict:
    check = self.context.get("check_failures") or {}
    if not check.get("failing_agents"):
        return {"skipped": True}
    data = {"failing_agents": check["failing_agents"], "error_details": check.get("error_details", {})}
    prompt_path = REPO_ROOT / "agents" / "prompts" / "librarian_watch.md"
    prompt = prompt_path.read_text().replace("{{DATA}}", json.dumps(data))
    text = self.synthesize(prompt).strip()
    if text.startswith("```"):
        text = re.sub(r'^```[a-z]*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text).strip()
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        text = match.group(0)
    findings = json.loads(text)
    self.context["findings"] = findings
    return {"findings": len(findings)}
```

- [ ] Run tests:
```
pytest tests/test_librarian.py -v
```
Expected: all pass

- [ ] Commit:
```bash
git add agents/librarian.py agents/prompts/librarian_audit.md agents/prompts/librarian_watch.md tests/test_librarian.py
git commit -m "feat: implement LLM analysis steps and apply_learnings"
```

---

### Task 6: propose_changes

**Files:**
- Modify: `agents/librarian.py`
- Modify: `tests/test_librarian.py`

- [ ] Add failing tests:

```python
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
```

- [ ] Run to confirm failures

- [ ] Implement `_propose_changes()`:

```python
def _propose_changes(self) -> dict:
    findings = self.context.get("findings") or []
    proposals_dir = REPO_ROOT / "output" / "librarian" / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    proposals = []
    for f in findings:
        conf = f.get("confidence", 0)
        if not (0.5 <= conf < 0.8 and f.get("fix_type") == "prompt_edit"):
            continue
        stem = PROMPT_STEMS.get(f["agent"])
        if not stem:
            continue
        pf = REPO_ROOT / "agents" / "prompts" / f"{stem}.md"
        if not pf.exists():
            continue
        pid = str(uuid.uuid4())[:8]
        proposal = {
            "id": pid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent": f["agent"],
            "finding": f.get("description", ""),
            "fix_type": "prompt_edit",
            "file": f"agents/prompts/{stem}.md",
            "original": pf.read_text(),
            "proposed": f.get("proposed_prompt_section", ""),
            "status": "pending",
        }
        (proposals_dir / f"{pid}.json").write_text(json.dumps(proposal, indent=2))
        proposals.append(proposal)
    self.context["proposals"] = proposals
    return {"proposals": len(proposals)}
```

- [ ] Run tests:
```
pytest tests/test_librarian.py -v
```
Expected: all pass

- [ ] Commit:
```bash
git add agents/librarian.py tests/test_librarian.py
git commit -m "feat: implement propose_changes with JSON proposal persistence"
```

---

### Task 7: HTML email + send_report + alert

**Files:**
- Create: `agents/prompts/librarian_report.md`
- Modify: `agents/librarian.py`
- Modify: `tests/test_librarian.py`

- [ ] Create `agents/prompts/librarian_report.md`:

```markdown
# Librarian Report — Send Email

The librarian run is complete. Use ToolSearch to load Gmail — search `"gmail gmail_send"`.

Send the HTML email below to cianohughes@gmail.com:
- Subject: `Librarian Report — {{TODAY}}`
- mimeType: `text/html`

Reply with only "sent" once dispatched. Do not summarise content.

{{HTML_EMAIL}}
```

- [ ] Add failing tests:

```python
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
```

- [ ] Run to confirm failures

- [ ] Implement `_build_html_report()`, `_send_report()`, `_alert()` in `agents/librarian.py`:

```python
def _build_html_report(self, today: str, mode: str) -> str:
    import html as hl
    import os
    findings = self.context.get("findings") or []
    applied = self.context.get("applied_learnings") or []
    proposals = self.context.get("proposals") or []
    check = self.context.get("check_failures") or {}
    token = os.environ.get("MCP_BRIDGE_TOKEN", "")
    sections = ""

    if mode == "watch":
        failing = check.get("failing_agents", [])
        errs = check.get("error_details", {})
        body = "".join(
            f'<p><b>{hl.escape(a)}</b>: {hl.escape(str(errs.get(a, [])))}</p>'
            for a in failing
        )
        sections += f'<h2>Failure Alert</h2>{body}'
    else:
        stats = self.context.get("collected", {}).get("agent_stats", {})
        rows = "".join(
            f'<tr><td>{hl.escape(a)}</td><td>{s["total_runs"]}</td>'
            f'<td style="color:{"red" if s["failure_rate"]>0.2 else "green"}">'
            f'{s["failure_rate"]:.0%}</td></tr>'
            for a, s in stats.items()
        )
        sections += (
            f'<h2>Agent Stats (30 days)</h2>'
            f'<table border="1" cellpadding="4">'
            f'<tr><th>Agent</th><th>Runs</th><th>Failure rate</th></tr>{rows}</table>'
        )

    if findings:
        items = "".join(
            f'<li><b>[{hl.escape(f.get("type","").upper())}]</b> '
            f'{hl.escape(f.get("agent",""))}: {hl.escape(f.get("description",""))} '
            f'(conf: {f.get("confidence",0):.0%})</li>'
            for f in findings
        )
        sections += f'<h2>Findings</h2><ul>{items}</ul>'

    if applied:
        items = "".join(
            f'<li><b>{hl.escape(a["agent"])}</b>: {hl.escape(a["entry"])}</li>'
            for a in applied
        )
        sections += f'<h2>Auto-applied Learnings</h2><ul>{items}</ul>'

    if proposals:
        cards = ""
        for p in proposals:
            pid = hl.escape(p["id"])
            approve = f"{BRIDGE_BASE}/librarian/approve?id={pid}&token={token}"
            reject = f"{BRIDGE_BASE}/librarian/reject?id={pid}&token={token}"
            cards += (
                f'<div style="border:1px solid #ccc;padding:12px;margin:8px 0">'
                f'<b>{hl.escape(p["agent"])}</b>: {hl.escape(p.get("finding",""))}<br/>'
                f'<a href="{approve}" style="background:#2ecc71;color:#fff;padding:6px 12px;'
                f'text-decoration:none;margin-right:8px">Approve</a>'
                f'<a href="{reject}" style="background:#e74c3c;color:#fff;padding:6px 12px;'
                f'text-decoration:none">Reject</a></div>'
            )
        sections += f'<h2>Proposed Changes</h2>{cards}'

    return (
        f'<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto">'
        f'<h1>Librarian {hl.escape(mode.title())} — {hl.escape(today)}</h1>'
        f'{sections}'
        f'<hr/><p style="color:#aaa;font-size:12px">Generated by LibrarianAgent</p>'
        f'</body></html>'
    )


def _send_report(self) -> dict:
    today = self.context["plan"]["today"]
    html_email = self._build_html_report(today, "audit")
    prompt_path = REPO_ROOT / "agents" / "prompts" / "librarian_report.md"
    prompt = prompt_path.read_text().replace("{{TODAY}}", today).replace("{{HTML_EMAIL}}", html_email)
    self.synthesize(prompt)
    return {"sent": True}


def _alert(self) -> dict:
    check = self.context.get("check_failures") or {}
    if not check.get("failing_agents"):
        return {"skipped": True, "reason": "no_failures"}
    today = self.context["plan"]["today"]
    html_email = self._build_html_report(today, "watch")
    prompt_path = REPO_ROOT / "agents" / "prompts" / "librarian_report.md"
    prompt = prompt_path.read_text().replace("{{TODAY}}", today).replace("{{HTML_EMAIL}}", html_email)
    self.synthesize(prompt)
    return {"sent": True}
```

- [ ] Run tests:
```
pytest tests/test_librarian.py -v
```
Expected: all pass

- [ ] Commit:
```bash
git add agents/librarian.py agents/prompts/librarian_report.md tests/test_librarian.py
git commit -m "feat: implement HTML email builder, send_report and alert steps"
```

---

### Task 8: Bridge server approve/reject routes

**Files:**
- Modify: `mcp-servers/bridge_server.py`

The bridge server is a plain `BaseHTTPRequestHandler`. It currently only handles `do_POST`. Add `do_GET` for the two new routes.

- [ ] Add the two handler functions after the existing `tool_*` functions in `bridge_server.py`:

```python
def _handle_librarian_approve(proposal_id: str) -> tuple[int, str]:
    proposals_dir = REPO_ROOT / "output" / "librarian" / "proposals"
    pf = proposals_dir / f"{proposal_id}.json"
    if not pf.exists():
        return 404, "<h1>Proposal not found</h1>"
    proposal = json.loads(pf.read_text())
    if proposal.get("status") != "pending":
        return 400, f"<h1>Already {proposal['status']}</h1>"
    target = REPO_ROOT / proposal["file"]
    target.write_text(proposal["proposed"])
    import subprocess as _sp
    _sp.run(["git", "add", proposal["file"]], cwd=str(REPO_ROOT), check=True)
    _sp.run(["git", "commit", "-m", f"librarian: apply proposal {proposal_id}"],
            cwd=str(REPO_ROOT), check=True)
    proposal["status"] = "approved"
    pf.write_text(json.dumps(proposal, indent=2))
    return 200, f"<h1>&#10003; Approved</h1><p>Applied to <code>{proposal['file']}</code> and committed.</p>"


def _handle_librarian_reject(proposal_id: str) -> tuple[int, str]:
    proposals_dir = REPO_ROOT / "output" / "librarian" / "proposals"
    pf = proposals_dir / f"{proposal_id}.json"
    if not pf.exists():
        return 404, "<h1>Proposal not found</h1>"
    proposal = json.loads(pf.read_text())
    proposal["status"] = "rejected"
    pf.write_text(json.dumps(proposal, indent=2))
    return 200, "<h1>&#10007; Rejected</h1><p>No changes were made.</p>"
```

- [ ] Add `do_GET` method to the handler class (add it alongside `do_POST`):

```python
def do_GET(self):
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(self.path)
    params = parse_qs(parsed.query)
    token = params.get("token", [""])[0]
    if token != BRIDGE_TOKEN:
        self.send_response(401)
        self.end_headers()
        self.wfile.write(b"Unauthorized")
        return
    proposal_id = params.get("id", [""])[0]
    if not re.match(r'^[a-f0-9-]{8,36}$', proposal_id):
        self.send_response(400)
        self.end_headers()
        self.wfile.write(b"Invalid id")
        return
    if parsed.path == "/librarian/approve":
        status, body = _handle_librarian_approve(proposal_id)
    elif parsed.path == "/librarian/reject":
        status, body = _handle_librarian_reject(proposal_id)
    else:
        status, body = 404, "<h1>Not found</h1>"
    self.send_response(status)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.end_headers()
    self.wfile.write(body.encode())
```

- [ ] Also add `"librarian"` to `AGENT_REGISTRY` in bridge_server.py:

```python
AGENT_REGISTRY = ["daily-briefing", "news-briefing", "security-audit", "librarian"]
```

- [ ] Smoke test (no unit test for HTTP handler — thin integration layer):

```bash
# In one terminal:
MCP_BRIDGE_TOKEN=testtoken python3 mcp-servers/bridge_server.py

# In another terminal, create a fake proposal:
mkdir -p output/librarian/proposals
cat > output/librarian/proposals/abcd1234.json <<'EOF'
{"id":"abcd1234","agent":"news-briefing","file":"agents/prompts/news_briefing.md",
 "original":"original content","proposed":"proposed content",
 "status":"pending","finding":"test","fix_type":"prompt_edit","created_at":"2026-05-22T06:00:00Z"}
EOF

# Approve it:
curl "http://localhost:4242/librarian/approve?id=abcd1234&token=testtoken"
# Expected: 200 HTML with "Approved", file changed, git commit created

# Reject an already-approved proposal:
curl "http://localhost:4242/librarian/approve?id=abcd1234&token=testtoken"
# Expected: 400 "Already approved"
```

- [ ] Commit:
```bash
git add mcp-servers/bridge_server.py
git commit -m "feat: add librarian approve/reject GET routes to bridge server"
```

---

### Task 9: Gitignore, CLAUDE.md, cron

**Files:**
- Modify: `.gitignore`
- Modify: `CLAUDE.md`
- Manual cron edit

- [ ] Add to `.gitignore`:

```
docs/agent-learnings/
output/librarian/
```

- [ ] Update `CLAUDE.md` — add to the agents list under `agents/`:

```
│   ├── librarian.py         # Librarian agent (on-demand, cron-managed: audit Sun 06:00 UTC, watch Mon-Sat 06:00 UTC). Reads agent run history + output files, calls LLM to produce findings. Auto-applies learnings (confidence ≥0.8), emails prompt proposals (0.5-0.79) with approve/reject links via bridge server.
```

Add to `agents/prompts/`:
```
│       ├── librarian_audit.md   # Weekly full analysis prompt
│       ├── librarian_watch.md   # Daily failure-scan prompt
│       └── librarian_report.md  # Email send prompt
```

- [ ] Add cron entries manually (NOT via `install-cron` — mode args require manual entries):

```bash
crontab -e
# Add inside # --- ai-agents managed --- block:
0 6 * * 0 /home/cian/git/ai-agents/run-agent.sh librarian --mode audit >> /home/cian/git/ai-agents/output/cron.log 2>&1
0 6 * * 1-6 /home/cian/git/ai-agents/run-agent.sh librarian --mode watch >> /home/cian/git/ai-agents/output/cron.log 2>&1
```

- [ ] Commit:
```bash
git add .gitignore CLAUDE.md
git commit -m "docs: add librarian to CLAUDE.md, gitignore agent-learnings and proposals"
```

---

### Task 10: Full test suite + integration smoke test

- [ ] Run full test suite:
```
pytest tests/ -v
```
Expected: all pass

- [ ] Run watch mode with no failures (should be a no-op, zero LLM calls):
```bash
python3 -m agents run librarian --mode watch
```
Expected output: `Librarian watch for <date> complete`
Expected: no email sent, check_failures returns empty list

- [ ] Verify run was recorded in DB:
```bash
python3 -m agents history librarian
```
Expected: 1 row with status `success`

- [ ] Run audit mode end-to-end (makes real LLM calls, sends real email):
```bash
python3 -m agents run librarian --mode audit
```
Expected: email arrives at cianohughes@gmail.com with subject `Librarian Report — <date>`, stats table visible, any findings listed
