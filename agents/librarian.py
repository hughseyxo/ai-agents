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
        findings = json.loads(text)
        self.context["findings"] = findings
        return {"findings": len(findings)}


    def _apply_learnings(self) -> dict:
        findings = self.context.get("findings") or []
        ld = REPO_ROOT / "docs" / "agent-learnings"
        ld.mkdir(parents=True, exist_ok=True)
        mem_file = REPO_ROOT / "docs" / "librarian-memory.md"
        
        applied = []
        for f in findings:
            conf = f.get("confidence", 0)
            ft = f.get("fix_type")
            entry = f.get("learnings_entry", "").strip()
            
            if conf >= 0.8 and ft == "learnings" and entry:
                lf = ld / f"{f['agent']}.md"
                existing = lf.read_text().strip() if lf.exists() else ""
                if entry not in existing:
                    lf.write_text((existing + "\n" + entry).strip() + "\n")
                    applied.append({"agent": f["agent"], "entry": entry})
            
            elif conf >= 0.8 and ft == "memory_update" and entry:
                existing = mem_file.read_text().strip() if mem_file.exists() else ""
                if entry not in existing:
                    mem_file.write_text((existing + "\n" + entry).strip() + "\n")
                    applied.append({"agent": "global", "entry": entry})

        self.context["applied_learnings"] = applied
        return {"applied": len(applied)}
    def _propose_changes(self) -> dict:
        findings = self.context.get("findings") or []
        proposals_dir = REPO_ROOT / "output" / "librarian" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposals = []
        for f in findings:
            conf = f.get("confidence", 0)
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
                (proposals_dir / f"{pid}.json").write_text(json.dumps(proposal, indent=2))
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
                (proposals_dir / f"{pid}.json").write_text(json.dumps(proposal, indent=2))
                proposals.append(proposal)

        self.context["proposals"] = proposals
        return {"proposals": len(proposals)}

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
                ft = p.get("fix_type")
                
                if ft == "prompt_edit":
                    approve = f"{BRIDGE_BASE}/librarian/approve?id={pid}&token={token}"
                    reject = f"{BRIDGE_BASE}/librarian/reject?id={pid}&token={token}"
                    label = "Approve Prompt Edit"
                elif ft == "architecture_plan":
                    approve = f"{BRIDGE_BASE}/librarian/create_plan?id={pid}&token={token}"
                    reject = f"{BRIDGE_BASE}/librarian/reject?id={pid}&token={token}"
                    label = "Approve Plan Creation"
                else:
                    continue

                cards += (
                    f'<div style="border:1px solid #ccc;padding:12px;margin:8px 0">'
                    f'<b>{hl.escape(p["agent"])}</b>: {hl.escape(p.get("finding",""))}<br/>'
                    f'<a href="{approve}" style="background:#2ecc71;color:#fff;padding:6px 12px;'
                    f'text-decoration:none;margin-right:8px">{label}</a>'
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
        findings = json.loads(text)
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
