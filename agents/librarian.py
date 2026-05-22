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
    def _analyze(self): raise NotImplementedError
    def _apply_learnings(self): raise NotImplementedError
    def _propose_changes(self): raise NotImplementedError
    def _send_report(self): raise NotImplementedError

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
