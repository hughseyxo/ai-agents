"""Librarian Agent — audits and improves other agents.

Cron (manual — uses --mode args not supported by install-cron):
  0 6 * * 0    run-agent.sh librarian --mode audit    # Sunday full audit
  0 6 * * 1-6  run-agent.sh librarian --mode watch   # Mon-Sat failure check
"""
import json
import logging
import re
import subprocess
import uuid
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .base import BaseAgent, REPO_ROOT
from .plant_profiles import parse_frontmatter as _parse_fm
from .plant_profiles import write_profile_atomic as _write_atomic

logger = logging.getLogger(__name__)


def _safe_component(name: str) -> str:
    """Slugify an untrusted name into a single safe path component ([a-z0-9-]),
    rejecting path-traversal (C3). Raises ValueError if nothing usable remains."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    if not slug or slug in (".", ".."):
        raise ValueError(f"unsafe path component: {name!r}")
    return slug


def _coerce_conf(value) -> float:
    """Coerce an LLM-supplied confidence to float (C5); non-numeric → 0.0 so it
    never auto-applies and never raises on a `< threshold` comparison."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_findings_json(text: str) -> list:
    """Parse an LLM findings response into a list (C4). On malformed JSON or a
    non-list payload, log and return [] so the audit fails safe instead of raising."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("librarian: failed to parse findings JSON: %s", e)
        return []
    if not isinstance(data, list):
        logger.error("librarian: findings JSON is not a list (got %s)", type(data).__name__)
        return []
    return data


def _write_learning_note(
    agent: str, entry: str, confidence: float, slug: str,
    related: list, note_type: str = "learnings"
) -> Path:
    """Write an atomic status-tagged note for a librarian finding.
    Returns the path written. Overwrites if the same slug already exists.
    note_type='memory' → docs/librarian-memory/<date>-<slug>.md
    note_type='learnings' → docs/agent-learnings/<agent>/<date>-<slug>.md
    """
    today = datetime.now(timezone.utc).date().isoformat()
    if note_type == "memory":
        parent = REPO_ROOT / "docs" / "librarian-memory"
    else:
        # C3: agent comes from untrusted LLM findings — bound it to a single
        # safe path component so it cannot escape docs/agent-learnings/.
        parent = REPO_ROOT / "docs" / "agent-learnings" / _safe_component(agent)
    parent.mkdir(parents=True, exist_ok=True)

    filename = f"{today}-{_safe_component(slug)}.md"
    path = parent / filename
    # Defence in depth: the sanitised components can't escape, but verify anyway.
    if parent.resolve() not in path.resolve().parents:
        raise ValueError(f"learning-note path escapes {parent}")

    fm = yaml.safe_dump({
        "type": note_type,
        "agent": agent,
        "confidence": confidence,
        "status": "active",
        "date": today,
        "tags": [agent, note_type],
        "related": related,
    }, sort_keys=False, default_flow_style=False).strip()

    _write_atomic(path, f"---\n{fm}\n---\n\n## Note\n\n{entry}\n")
    return path


_LEARNING_TYPES = {"learnings", "memory"}

def _collect_learnings(learnings_dir: Path) -> dict:
    """Glob `learnings_dir/**/*.md`, parse frontmatter, return {path_str: body}
    for files where status == 'active' (missing status treated as active for back-compat).
    Index/dashboard notes (type: index, type: dashboard) are skipped."""
    result = {}
    if not learnings_dir.is_dir():
        return result
    for md in sorted(learnings_dir.glob("**/*.md")):
        try:
            meta, body = _parse_fm(md.read_text())
        except Exception:
            continue
        note_type = meta.get("type", "learnings")
        if note_type not in _LEARNING_TYPES:
            continue
        status = meta.get("status", "active")
        if status == "active":
            result[str(md)] = body.strip()
    return result

AGENT_NAMES = ["daily-briefing", "news-briefing", "security-audit", "librarian", "telegram-bot", "plant-agent"]

OUTPUT_PATTERNS = {
    "daily-briefing": "daily-briefing-*.html",
    "news-briefing": "daily-news-briefing-*.md",
    "security-audit": "security-audit-*.md",
}

PROMPT_STEMS = {
    "daily-briefing": "daily_briefing",
    "news-briefing": "news_briefing",
    "librarian": "librarian_audit",
    "plant-agent": "plant_intelligence",
}

# Agents whose source lives outside agents/{stem}.py
SOURCE_CODE_OVERRIDES = {
    "telegram-bot": [
        REPO_ROOT / "telegram-bot" / "bot.py",
        REPO_ROOT / "telegram-bot" / "tools.py",
        REPO_ROOT / "telegram-bot" / "antigravity_backend.py",
        REPO_ROOT / "telegram-bot" / "claude_backend.py",
        REPO_ROOT / "telegram-bot" / "tool_specs.py",
    ],
}

MCP_SERVER_FILES = [
    REPO_ROOT / "mcp-servers" / "gmail_server.py",
    REPO_ROOT / "mcp-servers" / "calendar_server.py",
]

_PROPOSAL_ID_RE = re.compile(r"^[a-f0-9-]{8,36}$")


def _proposals_dir() -> Path:
    return REPO_ROOT / "output" / "librarian" / "proposals"


def _git_commit(rel_path: str, message: str) -> None:
    """Stage and commit a single file. Raises CalledProcessError on failure."""
    subprocess.run(["git", "add", rel_path], cwd=str(REPO_ROOT), check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(REPO_ROOT), check=True)


def _load_proposal(proposal_id: str):
    """Return (path, dict) for a pending-or-any proposal, or None if the id is
    malformed or the file is missing. Validates the id shape before any I/O."""
    if not _PROPOSAL_ID_RE.match(proposal_id):
        print(f"[librarian] invalid proposal id: {proposal_id!r}", file=sys.stderr)
        return None
    pf = _proposals_dir() / f"{proposal_id}.json"
    if not pf.exists():
        print(f"[librarian] proposal not found: {proposal_id}", file=sys.stderr)
        return None
    return pf, json.loads(pf.read_text())


def apply_proposal(proposal_id: str) -> int:
    """Apply a pending prompt_edit proposal: write the proposed prompt to its file
    (bounds-checked under REPO_ROOT), commit it, and mark the proposal approved.
    Returns 0 on success, non-zero otherwise. Replaces the old bridge approve link."""
    loaded = _load_proposal(proposal_id)
    if loaded is None:
        return 1
    pf, proposal = loaded
    if proposal.get("status") != "pending":
        print(f"[librarian] proposal already {proposal.get('status')}", file=sys.stderr)
        return 1
    if proposal.get("fix_type") != "prompt_edit":
        print(f"[librarian] apply only supports prompt_edit (got {proposal.get('fix_type')}); "
              f"use create-plan for architecture_plan", file=sys.stderr)
        return 1

    rel = proposal.get("file", "")
    target = (REPO_ROOT / rel).resolve()
    try:
        target.relative_to(REPO_ROOT.resolve())
    except ValueError:
        print(f"[librarian] proposal file escapes repo: {rel!r}", file=sys.stderr)
        return 1

    _write_atomic(target, proposal.get("proposed", ""))
    try:
        _git_commit(rel, f"librarian: apply proposal {proposal_id}")
    except Exception as e:
        print(f"[librarian] git commit failed: {e}", file=sys.stderr)
        return 1
    proposal["status"] = "approved"
    _write_atomic(pf, json.dumps(proposal, indent=2))
    print(f"[librarian] applied proposal {proposal_id} to {rel} and committed.")
    return 0


def reject_proposal(proposal_id: str) -> int:
    """Mark a proposal rejected without changing any source file. Returns 0/1."""
    loaded = _load_proposal(proposal_id)
    if loaded is None:
        return 1
    pf, proposal = loaded
    proposal["status"] = "rejected"
    _write_atomic(pf, json.dumps(proposal, indent=2))
    print(f"[librarian] rejected proposal {proposal_id}.")
    return 0


def create_plan_from_proposal(proposal_id: str) -> int:
    """Materialise an architecture_plan proposal into docs/superpowers/plans/. Returns 0/1."""
    loaded = _load_proposal(proposal_id)
    if loaded is None:
        return 1
    pf, proposal = loaded
    if proposal.get("status") != "pending":
        print(f"[librarian] proposal already {proposal.get('status')}", file=sys.stderr)
        return 1
    plans_dir = REPO_ROOT / "docs" / "superpowers" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan_file = plans_dir / f"{today}-librarian-arch-{proposal_id}.md"
    content = (
        f"# Architectural Plan: {proposal.get('agent', '')}\n\n"
        f"**ID**: {proposal_id}\n"
        f"**Finding**: {proposal.get('finding', '')}\n\n"
        f"{proposal.get('proposed_plan', 'No plan provided.')}\n"
    )
    _write_atomic(plan_file, content)
    proposal["status"] = "plan_created"
    proposal["plan_file"] = str(plan_file.relative_to(REPO_ROOT))
    _write_atomic(pf, json.dumps(proposal, indent=2))
    print(f"[librarian] wrote plan {proposal['plan_file']}.")
    return 0


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

        source_code: dict[str, str] = {}
        for agent_name in AGENT_NAMES:
            if agent_name in SOURCE_CODE_OVERRIDES:
                parts = []
                for path in SOURCE_CODE_OVERRIDES[agent_name]:
                    if path.exists():
                        parts.append(f"# {path.name}\n{path.read_text()[:8000]}")
                if parts:
                    source_code[agent_name] = "\n\n".join(parts)
            else:
                stem = agent_name.replace("-", "_")
                py_file = REPO_ROOT / "agents" / f"{stem}.py"
                if py_file.exists():
                    source_code[agent_name] = py_file.read_text()[:15000]

        mcp_source: dict[str, str] = {}
        for path in MCP_SERVER_FILES:
            if path.exists():
                mcp_source[path.name] = path.read_text()[:8000]

        import subprocess as _sp
        try:
            git_log = _sp.check_output(
                ["git", "log", "--oneline", "--since=14 days ago", "--name-only", "--no-merges"],
                cwd=str(REPO_ROOT), text=True, timeout=10,
            )[:4000]
        except Exception:
            git_log = ""

        mem_file = REPO_ROOT / "docs" / "librarian-memory.md"
        arch_memory = mem_file.read_text() if mem_file.exists() else ""

        self.context["collected"] = {
            "agent_stats": agent_stats,
            "output_samples": output_samples,
            "prompts": prompts,
            "learnings": learnings,
            "source_code": source_code,
            "mcp_source": mcp_source,
            "recent_git_log": git_log,
            "arch_memory": arch_memory,
        }
        return {"agents_analysed": len(agent_stats)}

    def _analyze(self) -> dict:
        collected = self.context.get("collected", {})
        prompt_path = REPO_ROOT / "agents" / "prompts" / "librarian_audit.md"
        prompt = prompt_path.read_text().replace("{{DATA}}", json.dumps(collected))
        text = self.synthesize(prompt).strip()
        match = re.search(r'```json\s*(\[[\s\S]*?\])\s*```', text)
        if match:
            text = match.group(1)
        elif text.startswith("```"):
            text = re.sub(r'^```[a-z]*\n?', '', text)
            text = re.sub(r'\n?```\s*$', '', text).strip()
            match = re.search(r'\[[\s\S]*\]', text)
            if match:
                text = match.group(0)
        else:
            match = re.search(r'\[[\s\S]*\]', text)
            if match:
                text = match.group(0)
        findings = _parse_findings_json(text)  # C4: fail safe on bad LLM JSON
        self.context["findings"] = findings
        return {"findings": len(findings)}


    def _apply_learnings(self) -> dict:
        findings = self.context.get("findings") or []
        applied = []
        for f in findings:
            conf = _coerce_conf(f.get("confidence", 0))  # C5: never raise on non-numeric
            ft = f.get("fix_type")
            entry = f.get("learnings_entry", "").strip()
            # C5: only auto-apply known low-risk fix types at high confidence.
            if not entry or conf < 0.8 or ft not in ("learnings", "memory_update"):
                continue
            slug = f.get("slug") or re.sub(r"[^a-z0-9]+", "-", entry[:40].lower()).strip("-")
            related = f.get("related", [])
            try:
                if ft == "learnings":
                    agent = f.get("agent")
                    if not agent:
                        continue
                    _write_learning_note(agent, entry, conf, slug, related, note_type="learnings")
                    applied.append({"agent": agent, "entry": entry})
                else:  # memory_update
                    _write_learning_note("global", entry, conf, slug, related, note_type="memory")
                    applied.append({"agent": "global", "entry": entry})
            except ValueError as e:
                # C3: hostile agent/slug name — skip this finding, keep going.
                logger.warning("librarian: skipping learning note: %s", e)
                continue
        self.context["applied_learnings"] = applied
        return {"applied": len(applied)}
    def _propose_changes(self) -> dict:
        findings = self.context.get("findings") or []
        proposals_dir = REPO_ROOT / "output" / "librarian" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposals = []
        for f in findings:
            conf = _coerce_conf(f.get("confidence", 0))  # C5
            ft = f.get("fix_type")

            # prompt_edit and architecture_plan always need human review regardless of confidence.
            # _apply_learnings handles "learnings"/"memory_update" at conf >= 0.8 separately.
            if conf < 0.5 or ft not in ("prompt_edit", "architecture_plan"):
                continue

            if ft == "prompt_edit":
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
                _write_atomic(proposals_dir / f"{pid}.json", json.dumps(proposal, indent=2))
                proposals.append(proposal)
            
            elif ft == "architecture_plan":
                pid = str(uuid.uuid4())[:8]
                proposal = {
                    "id": pid,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "agent": f["agent"],
                    "finding": f.get("description", ""),
                    "fix_type": "architecture_plan",
                    "proposed_plan": f.get("suggested_plan", ""),
                    "status": "pending",
                }
                _write_atomic(proposals_dir / f"{pid}.json", json.dumps(proposal, indent=2))
                proposals.append(proposal)

        self.context["proposals"] = proposals
        return {"proposals": len(proposals)}

    def _build_html_report(self, today: str, mode: str) -> str:
        import html as hl
        findings = self.context.get("findings") or []
        applied = self.context.get("applied_learnings") or []
        proposals = self.context.get("proposals") or []
        check = self.context.get("check_failures") or {}
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
                ft = p.get("fix_type")

                if ft == "prompt_edit":
                    apply_cmd = f"python3 -m agents librarian-apply {pid}"
                elif ft == "architecture_plan":
                    apply_cmd = f"python3 -m agents librarian-plan {pid}"
                else:
                    continue
                reject_cmd = f"python3 -m agents librarian-reject {pid}"

                # Approvals are now applied via a local CLI on the server (the MCP
                # bridge that served one-click links was removed). Show the commands.
                cards += (
                    f'<div style="border:1px solid #ccc;padding:12px;margin:8px 0">'
                    f'<b>{hl.escape(p["agent"])}</b>: {hl.escape(p.get("finding",""))}<br/>'
                    f'<p style="margin:6px 0 2px">Apply: <code>{hl.escape(apply_cmd)}</code></p>'
                    f'<p style="margin:2px 0">Reject: <code>{hl.escape(reject_cmd)}</code></p>'
                    f'</div>'
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
        now = datetime.now().strftime("%H:%M")
        html_email = self._build_html_report(today, "audit")
        prompt_path = REPO_ROOT / "agents" / "prompts" / "librarian_report.md"
        prompt = prompt_path.read_text().replace("{{TODAY}}", f"{today} {now}").replace("{{HTML_EMAIL}}", html_email)
        self.synthesize(prompt)
        return {"sent": True}

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
        data = {"failing_agents": check["failing_agents"], "error_details": check.get("error_details", {})}
        prompt_path = REPO_ROOT / "agents" / "prompts" / "librarian_watch.md"
        prompt = prompt_path.read_text().replace("{{DATA}}", json.dumps(data))
        text = self.synthesize(prompt).strip()
        match = re.search(r'```json\s*(\[[\s\S]*?\])\s*```', text)
        if match:
            text = match.group(1)
        elif text.startswith("```"):
            text = re.sub(r'^```[a-z]*\n?', '', text)
            text = re.sub(r'\n?```\s*$', '', text).strip()
            match = re.search(r'\[[\s\S]*\]', text)
            if match:
                text = match.group(0)
        else:
            match = re.search(r'\[[\s\S]*\]', text)
            if match:
                text = match.group(0)
        findings = _parse_findings_json(text)  # C4: fail safe on bad LLM JSON
        self.context["findings"] = findings
        return {"findings": len(findings)}

    def _alert(self) -> dict:
        check = self.context.get("check_failures") or {}
        if not check.get("failing_agents"):
            return {"skipped": True, "reason": "no_failures"}
        today = self.context["plan"]["today"]
        now = datetime.now().strftime("%H:%M")
        html_email = self._build_html_report(today, "watch")
        prompt_path = REPO_ROOT / "agents" / "prompts" / "librarian_report.md"
        prompt = prompt_path.read_text().replace("{{TODAY}}", f"{today} {now}").replace("{{HTML_EMAIL}}", html_email)
        self.synthesize(prompt)
        return {"sent": True}
