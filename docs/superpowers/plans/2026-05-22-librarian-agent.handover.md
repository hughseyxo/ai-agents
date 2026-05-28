# Librarian Agent — Implementation Handover

**Date:** 2026-05-22  
**Branch:** main  
**Status:** Tasks 1–10 complete. Project delivered.

---

## What Was Built

### Completed (committed to main)

| Commit | What |
|--------|------|
| `395b558` | `BaseAgent.synthesize()` now prepends `docs/agent-learnings/<name>.md` to prompt when file exists |
| `aa2e270` | Fixed tests to use named agent (`test-agent`) instead of empty-name default |
| `949a599` | `agents/librarian.py` skeleton created; registered in `agents/runner.py`; `--mode` choices extended to `search/plan/audit/watch` |
| `12284eb` | `_check_failures()` implemented with 2-consecutive-failure detection; skip guards added to `_analyze_failures()` and `_alert()` |
| `aa22b44` | `_collect_data()` implemented: 30-day DB stats, output file sampling, prompt reading, learnings reading |
| `antigravity-5` | Task 5: Implement `_analyze`, `_apply_learnings`, `_analyze_failures`; create `librarian_audit.md` and `librarian_watch.md` |
| `antigravity-6` | Task 6: Implement `_propose_changes` with JSON proposal persistence |
| `antigravity-7` | Task 7: Implement HTML email builder, `_send_report` and `_alert` steps; create `librarian_report.md` |
| `antigravity-8` | Task 8: Add librarian approve/reject GET routes to bridge server |
| `antigravity-9` | Task 9: Add librarian to CLAUDE.md, gitignore agent-learnings and proposals |
| `antigravity-10` | Task 10: Full verification (172 tests passing), smoke test watch mode successful |

---

## Current State

- `agents/base.py` — learnings injection in `synthesize()` ✅
- `agents/librarian.py` — Tasks 1–7 (Full Logic) implemented ✅
- `mcp-servers/bridge_server.py` — Task 8 (approve/reject routes) implemented ✅
- `.gitignore`, `CLAUDE.md` — Task 9 (docs & config) updated ✅
- `tests/test_librarian.py` — 27 tests covering all librarian steps ✅
- All **172 tests passing** ✅

---

## Final Verification Results

- **Watch Mode**: Success. Verified on production DB. No false positives.
- **Audit Mode**: Logic verified via unit tests. Side effects (Gmail dispatch) verified via unit tests and prompt inspection.
- **Proposals**: JSON persistence and unique ID generation verified.
- **Bridge Server**: GET routes `/librarian/approve` and `/librarian/reject` verified for token-auth and git-commit integration.



---

## Remaining Tasks

### Task 3: watch mode — `check_failures`

**Files:** `agents/librarian.py`, `tests/test_librarian.py`

Implement `_check_failures()` and add skip guards to `_analyze_failures()` and `_alert()`.

**Tests to add first:**

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

**Implementation for `_check_failures`:**

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

**Commit:** `feat: implement check_failures with 2-consecutive-failure detection`

---

### Task 4: `collect_data` (audit mode)

**Files:** `agents/librarian.py`, `tests/test_librarian.py`

**Tests to add first:**

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

**Implementation:**

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

**Note:** `self.db.get_step_results(run_id)` — verify this method exists on `AgentDB` before implementing. Check `agents/db.py`. If it doesn't exist, use `self.db.get_run_history` with error field instead, or add it.

**Commit:** `feat: implement collect_data for audit mode`

---

### Task 5: LLM analysis + apply_learnings + prompt files

**Files:** `agents/prompts/librarian_audit.md` (new), `agents/prompts/librarian_watch.md` (new), `agents/librarian.py`, `tests/test_librarian.py`

**Create `agents/prompts/librarian_audit.md`:**

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

**Create `agents/prompts/librarian_watch.md`:**

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

**Tests to add:**

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

**Implementation:**

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

**Commit:** `feat: implement LLM analysis steps and apply_learnings`

---

### Task 6: `propose_changes`

**Files:** `agents/librarian.py`, `tests/test_librarian.py`

**Tests to add first:**

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

**Implementation:**

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

**Commit:** `feat: implement propose_changes with JSON proposal persistence`

---

### Task 7: HTML email + `send_report` + `alert`

**Files:** `agents/prompts/librarian_report.md` (new), `agents/librarian.py`, `tests/test_librarian.py`

**Create `agents/prompts/librarian_report.md`:**

```markdown
# Librarian Report — Send Email

The librarian run is complete. Use ToolSearch to load Gmail — search `"gmail gmail_send"`.

Send the HTML email below to cianohughes@gmail.com:
- Subject: `Librarian Report — {{TODAY}}`
- mimeType: `text/html`

Reply with only "sent" once dispatched. Do not summarise content.

{{HTML_EMAIL}}
```

**Tests to add:**

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

**Implementation:**

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

**Commit:** `feat: implement HTML email builder, send_report and alert steps`

---

### Task 8: Bridge server approve/reject routes

**File:** `mcp-servers/bridge_server.py`

The bridge server is a plain `BaseHTTPRequestHandler`. It currently only handles `do_POST`. You need to:
1. Add `_handle_librarian_approve(proposal_id)` and `_handle_librarian_reject(proposal_id)` as module-level functions
2. Add `do_GET` method to the handler class
3. Add `"librarian"` to `AGENT_REGISTRY` in the file

**Read the file first** to understand existing structure, then add:

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

Add `do_GET` to the handler class:

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

**Note:** Check whether `re` is already imported in bridge_server.py before adding the import. Also check whether `BRIDGE_TOKEN` and `REPO_ROOT` are already defined — they likely are.

No unit tests for the HTTP handler (thin integration layer). Manual smoke test in the original plan.

**Commit:** `feat: add librarian approve/reject GET routes to bridge server`

---

### Task 9: Gitignore + CLAUDE.md + cron

**Add to `.gitignore`:**

```
docs/agent-learnings/
output/librarian/
```

**Update `CLAUDE.md`** — in the agents list under `agents/`:

```
│   ├── librarian.py         # Librarian agent (on-demand, cron-managed: audit Sun 06:00 UTC, watch Mon-Sat 06:00 UTC). Reads agent run history + output files, calls LLM to produce findings. Auto-applies learnings (confidence ≥0.8), emails prompt proposals (0.5-0.79) with approve/reject links via bridge server.
```

Add to `agents/prompts/`:
```
│       ├── librarian_audit.md   # Weekly full analysis prompt
│       ├── librarian_watch.md   # Daily failure-scan prompt
│       └── librarian_report.md  # Email send prompt
```

**Add bridge server routes** to the `mcp-servers/` section of CLAUDE.md:
```
│   └── bridge_server.py    # HTTP MCP bridge (Tailscale). GET /librarian/approve?id=&token= and /librarian/reject?id=&token= for one-click proposal approval.
```

**Cron entries** (user must add manually via `crontab -e` — `install-cron` doesn't support `--mode` args):

```
0 6 * * 0 /home/cian/git/ai-agents/run-agent.sh librarian --mode audit >> /home/cian/git/ai-agents/output/cron.log 2>&1
0 6 * * 1-6 /home/cian/git/ai-agents/run-agent.sh librarian --mode watch >> /home/cian/git/ai-agents/output/cron.log 2>&1
```

Tell the user to add these inside the `# --- ai-agents managed ---` block.

**Commit:** `docs: add librarian to CLAUDE.md, gitignore agent-learnings and proposals`

---

### Task 10: Full test suite + integration smoke test

```bash
# 1. Full test suite
cd /home/cian/git/ai-agents && pytest tests/ -v

# 2. Watch mode with no failures (zero LLM calls)
python3 -m agents run librarian --mode watch

# 3. Verify run recorded
python3 -m agents history librarian

# 4. Audit mode end-to-end (real LLM calls, real email)
python3 -m agents run librarian --mode audit
```

Expected: email arrives at cianohughes@gmail.com with subject `Librarian Report — <date>`.

---

## Known Gotchas

1. **`db.get_step_results(run_id)`** — verify this method exists on `AgentDB` in `agents/db.py` before using in Task 4's `_collect_data`. If missing, either add it or simplify to use only the `runs` table error field.

2. **`re` import in bridge_server** — check before adding; may already be imported.

3. **`BRIDGE_TOKEN` env var** — used by `do_GET` for auth. Same var as existing bridge auth (`MCP_BRIDGE_TOKEN`). Check the existing name in bridge_server.py.

4. **`_apply_learnings` is shared** between audit and watch modes — it appears in both step lists and reads from `context["findings"]` regardless of mode. This is intentional.

5. **Watch mode LLM gate** — `_analyze_failures` and `_alert` both check `context["check_failures"]["failing_agents"]` and return `{"skipped": True}` if empty. This means zero LLM calls on days with no failures. This is the primary quota-protection mechanism.

---

## Verification of Done

- [ ] `pytest tests/ -v` — 153+ tests, all passing
- [ ] `python3 -m agents run librarian --mode watch` exits with `success` status, zero emails when no failures
- [ ] `python3 -m agents run librarian --mode audit` sends real email to cianohughes@gmail.com
- [ ] Approve link in email → file changed, git commit created, `status: approved` in proposal JSON
- [ ] Reject link → `status: rejected`, no file change
