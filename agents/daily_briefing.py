"""Daily Briefing Agent.

Python handles: lifecycle, SQLite state, dedup.
LLM CLI (with MCP access) handles: calendar/todoist fetching, formatting, email sending.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseAgent, REPO_ROOT


class DailyBriefingAgent(BaseAgent):
    name = "daily-briefing"
    schedule = "5 4 * * *"
    model = "claude-haiku-4-5"

    def configure(self, args):
        if getattr(args, "force", False):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self.db.delete_seen("email_sent", today, agent=self.name)

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
            {"name": "briefing", "fn": self._run_briefing, "side_effects": True},
        ]

    def report(self) -> str:
        today = self.context["plan"]["today"]
        return f"Briefing for {today} complete"

    # --- Briefing (Claude CLI with MCP) ---

    def _run_briefing(self):
        """Invoke Claude CLI to fetch data via MCP, format, and send email."""
        today = self.context["plan"]["today"]

        # Check dedup before spending tokens
        if self.is_duplicate("email_sent", today):
            print(f"[{self.name}] Email already sent today, skipping", file=sys.stderr)
            return {"skipped": True, "reason": "already_sent"}

        prompt = self._build_prompt(today)

        # Claude CLI has MCP access — it fetches calendar, todoist, formats, and sends
        output = self.synthesize(prompt)

        # Save HTML output (model emits the same HTML it sent via gmail_send)
        output_path = REPO_ROOT / "output" / f"daily-briefing-{today}.html"
        output_path.write_text(output)

        self.mark_seen("email_sent", today)
        return {"sent": True, "output_path": str(output_path)}

    def _build_prompt(self, today: str) -> str:
        """Build the Claude CLI prompt."""
        prompt_path = REPO_ROOT / "agents" / "prompts" / "daily_briefing.md"
        base_prompt = prompt_path.read_text()

        return f"""{base_prompt}

## Today's Date
{today}
"""
