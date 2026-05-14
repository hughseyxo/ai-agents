"""Daily Briefing Agent.

Python handles: lifecycle, SQLite state, plant watering logic, weather, dedup.
LLM CLI (with MCP access) handles: calendar/todoist fetching, formatting, email sending.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import BaseAgent, REPO_ROOT
from .plant_weather import adjust_watering_date
from .weather import fetch_weather

PERSONAL_PROJECT_ID = "6Crf3cH2RF5v86wc"


class DailyBriefingAgent(BaseAgent):
    name = "daily-briefing"
    schedule = "5 5 * * *"

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
            {"name": "weather", "fn": self._fetch_weather},
            {"name": "plants", "fn": self._check_plants},
            {"name": "briefing", "fn": self._run_briefing},
        ]

    # --- Weather (Python — deterministic, no LLM needed) ---

    def _fetch_weather(self):
        """Fetch current weather for plant watering adjustments."""
        weather = fetch_weather()
        self.context["weather"] = weather
        return weather

    def report(self) -> str:
        today = self.context["plan"]["today"]
        return f"Briefing for {today} complete"

    # --- Plant watering (Python — deterministic, no LLM needed) ---

    def _check_plants(self):
        """Calculate watering schedule with weather adjustments."""
        plants = self.get_state("plants")
        if plants is None:
            return {"plants": [], "tasks_to_create": []}

        weather = self.context.get("weather")
        today = datetime.now(timezone.utc).date()
        upcoming_watering = []
        tasks_to_create = []
        updated = False

        for plant in plants:
            last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
            base_date = last_watered + timedelta(days=plant["frequency_days"])

            # Apply weather adjustment if weather data is available
            if weather:
                next_water, reason = adjust_watering_date(
                    base_date, plant["frequency_days"], plant, weather)
            else:
                next_water = base_date
                reason = ""

            days_until = (next_water - today).days

            if days_until <= 7:
                entry = {
                    "name": plant["name"],
                    "next_water_date": next_water.isoformat(),
                    "days_until": days_until,
                }
                if reason:
                    entry["adjustment"] = reason
                upcoming_watering.append(entry)

                tasks_to_create.append({
                    "name": plant["name"],
                    "task_content": f"Water {plant['name']}",
                    "due_date": next_water.isoformat(),
                })
                plant["last_watered"] = next_water.isoformat()
                updated = True

        if updated:
            self.set_state("plants", plants)

        return {"plants": upcoming_watering, "tasks_to_create": tasks_to_create}

    # --- Briefing (Claude CLI with MCP) ---

    def _run_briefing(self):
        """Invoke Claude CLI to fetch data via MCP, format, and send email."""
        today = self.context["plan"]["today"]

        # Check dedup before spending tokens
        if self.is_duplicate("email_sent", today):
            print(f"[{self.name}] Email already sent today, skipping", file=sys.stderr)
            return {"skipped": True, "reason": "already_sent"}

        # Build the prompt with plant data injected
        plant_data = self.context.get("plants", {})
        prompt = self._build_prompt(today, plant_data)

        # Claude CLI has MCP access — it fetches calendar, todoist, formats, and sends
        output = self.synthesize(prompt)

        # Save markdown output
        output_path = REPO_ROOT / "output" / f"daily-briefing-{today}.md"
        output_path.write_text(output)

        self.mark_seen("email_sent", today)
        return {"sent": True, "output_path": str(output_path)}

    def _build_prompt(self, today: str, plant_data: dict) -> str:
        """Build the Claude CLI prompt, injecting pre-computed plant data."""
        prompt_path = REPO_ROOT / "agents" / "prompts" / "daily_briefing.md"
        base_prompt = prompt_path.read_text()

        plant_section = ""
        plants = plant_data.get("plants", [])
        tasks_to_create = plant_data.get("tasks_to_create", [])

        if plants:
            plant_lines = []
            for p in plants:
                line = f"- {p['next_water_date']} — Water {p['name']} (in {p['days_until']} days)"
                if p.get("adjustment"):
                    line += f" [adjusted: {p['adjustment']}]"
                plant_lines.append(line)
            plant_section = "\n".join(plant_lines)
        else:
            plant_section = "All plants are happy — no watering needed in the next 7 days."

        task_instructions = ""
        if tasks_to_create:
            task_lines = ["Create these Todoist tasks in project 6Crf3cH2RF5v86wc (if they don't already exist):"]
            for t in tasks_to_create:
                task_lines.append(f'- Content: "{t["task_content"]}", due: {t["due_date"]}, priority: p4')
            task_instructions = "\n".join(task_lines)

        return f"""{base_prompt}

## Pre-computed Plant Data
{plant_section}

## Plant Todoist Tasks
{task_instructions if task_instructions else "No plant tasks to create."}

## Today's Date
{today}
"""
