"""News Briefing Agent.

Python handles: lifecycle, dedup (prevent double email sends).
Claude CLI (with MCP access) handles: RSS fetching via WebFetch, article selection,
formatting, and email sending via Gmail MCP.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseAgent, REPO_ROOT


class NewsBriefingAgent(BaseAgent):
    name = "news-briefing"
    schedule = "0 5 * * *"

    def plan(self):
        last = self.last_run()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "last_run_date": last["started_at"][:10] if last else None,
            "already_ran_today": last is not None and last["started_at"][:10] == today,
            "today": today,
        }

    def steps(self):
        return [
            {"name": "news_briefing", "fn": self._run_briefing},
        ]

    def report(self) -> str:
        today = self.context["plan"]["today"]
        result = self.context.get("news_briefing", {})
        if result and result.get("skipped"):
            return f"News briefing for {today} skipped — {result['reason']}"
        return f"News briefing for {today} complete"

    def _run_briefing(self):
        """Invoke Claude CLI to fetch RSS, format, and send email."""
        today = self.context["plan"]["today"]

        if self.is_duplicate("email_sent", today):
            print(f"[{self.name}] Email already sent today, skipping", file=sys.stderr)
            return {"skipped": True, "reason": "already_sent"}

        prompt_path = REPO_ROOT / "agents" / "prompts" / "news_briefing.md"
        prompt = prompt_path.read_text().replace("{{TODAY}}", today)

        output = self.synthesize(prompt)

        output_path = REPO_ROOT / "output" / f"daily-news-briefing-{today}.md"
        output_path.write_text(output)

        self.mark_seen("email_sent", today)
        return {"sent": True, "output_path": str(output_path)}
