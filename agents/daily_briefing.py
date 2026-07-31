"""Daily Briefing Agent.

Python handles: lifecycle, SQLite state, dedup.
LLM CLI (with MCP access) handles: calendar/todoist fetching, formatting, email sending.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .base import BaseAgent, REPO_ROOT
from .plant_model import PlantStore


class DailyBriefingAgent(BaseAgent):
    name = "daily-briefing"
    schedule = "5 4 * * *"
    model = "claude-haiku-4-5"
    # Reads calendar + todoist and sends the briefing itself (see _run_briefing).
    # Gmail is scoped to send only — this agent never needs to read mail.
    mcp_config = ".mcp.json"
    # ToolSearch is required: MCP tools are deferred, so without it gmail_send
    # is never reachable and the briefing silently never sends.
    allowed_tools = ["ToolSearch", "mcp__todoist", "mcp__google-calendar",
                     "mcp__gmail__gmail_send"]

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

    def _build_plant_care_block(self, today: str) -> str:
        """Build a pre-computed plant care section for injection into the briefing prompt."""
        today_date = date.fromisoformat(today)
        store = PlantStore(self.db.db_path)
        plants = store.get_plants_raw()
        store.close()
        pending_actions = self.db.get_state("plant-agent", "pending_plant_actions") or []

        due_now = []
        upcoming = []

        for plant in plants:
            last_watered = plant.get("last_watered")
            freq = plant.get("frequency_days")
            if not last_watered or not freq:
                continue
            try:
                next_water = date.fromisoformat(last_watered) + timedelta(days=int(freq))
            except (ValueError, TypeError):
                continue
            days_until = (next_water - today_date).days
            if days_until <= 0:
                suffix = f" (overdue {abs(days_until)}d)" if days_until < 0 else " (due today)"
                due_now.append(f"Water {plant['name']}{suffix}")
            elif days_until <= 7:
                upcoming.append(f"Water {plant['name']} — {next_water.isoformat()}")

        for a in pending_actions:
            action_text = a.get("action", "").capitalize()
            if a.get("reason"):
                action_text += f" ({a['reason']})"
            due_now.append(f"{action_text} — {a['plant']}")

        if not due_now and not upcoming:
            return ""

        lines = [
            "## Plant Care Tasks",
            "Include these as [PLANT] items (🌱) in the Today and Coming Up sections.",
            "Style: green border (#3fb950), same as [TASK] items.",
        ]
        if due_now:
            lines.append("\nDue today / overdue / action needed:")
            lines.extend(f"- {item}" for item in due_now)
        if upcoming:
            lines.append("\nComing up (next 7 days):")
            lines.extend(f"- {item}" for item in upcoming)
        return "\n".join(lines)

    def _build_prompt(self, today: str) -> str:
        """Build the Claude CLI prompt."""
        prompt_path = REPO_ROOT / "agents" / "prompts" / "daily_briefing.md"
        base_prompt = prompt_path.read_text()
        plant_block = self._build_plant_care_block(today)

        return f"""{base_prompt}

## Today's Date
{today}

{plant_block}
"""
