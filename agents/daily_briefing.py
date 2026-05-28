"""Daily Briefing Agent.

Python handles: lifecycle, SQLite state, plant watering logic, weather, dedup.
LLM CLI (with MCP access) handles: calendar/todoist fetching, formatting, email sending.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import BaseAgent, REPO_ROOT
from .plant_weather import adjust_watering_date, is_heatwave_incoming
from .weather import fetch_weather

PERSONAL_PROJECT_ID = "6Crf3cH2RF5v86wc"

SENSITIVITY_THRESHOLDS = {"high": 0.8, "medium": 0.6, "low": 0.4}

_SYNC_PROMPT = f"""\
You are a data-fetch step. Your only job is to find recently completed plant watering tasks in Todoist and return JSON.

Use mcp__todoist__find-completed-tasks to find completed tasks in project {PERSONAL_PROJECT_ID} completed in the last 30 days.

Filter to tasks whose content starts with "Water " (case-insensitive).

Return ONLY valid JSON — no markdown, no explanation, no other text:
[{{"name": "Plant Name", "completed_date": "YYYY-MM-DD"}}]

Extract "Plant Name" by stripping the "Water " prefix from the task content.
Use the task's completion date as "completed_date" in YYYY-MM-DD format.
If no matching completed tasks found, return: []
"""


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
            {"name": "weather", "fn": self._fetch_weather},
            {"name": "sync_plant_completions", "fn": self._sync_plant_completions},
            {"name": "plants", "fn": self._check_plants},
            {"name": "briefing", "fn": self._run_briefing, "side_effects": True},
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

    def _sync_plant_completions(self):
        """Update last_watered for any plant whose Todoist task was marked done."""
        plants = self.get_state("plants")
        if not plants:
            return {"synced": 0}

        try:
            output = self.synthesize(_SYNC_PROMPT)
        except Exception as e:
            print(f"[{self.name}] sync_plant_completions: synthesize failed: {e}", file=sys.stderr)
            return {"synced": 0, "error": str(e)}

        text = output.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text.strip())

        try:
            completions = json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[{self.name}] sync_plant_completions: bad JSON ({e}): {text[:200]}", file=sys.stderr)
            return {"synced": 0, "error": "bad_json"}

        if not isinstance(completions, list):
            return {"synced": 0}

        plant_by_name = {p["name"].lower(): i for i, p in enumerate(plants)}
        updated = list(plants)
        synced = 0

        for entry in completions:
            name = entry.get("name", "")
            completed_date = entry.get("completed_date", "")
            if not name or not completed_date:
                continue
            idx = plant_by_name.get(name.lower())
            if idx is None:
                continue
            if completed_date > updated[idx]["last_watered"]:
                updated[idx] = {**updated[idx], "last_watered": completed_date}
                synced += 1

        if synced:
            self.set_state("plants", updated)

        return {"synced": synced}

    def _check_plants(self):
        """Calculate watering schedule with weather adjustments."""
        plants = self.get_state("plants")
        if plants is None:
            return {"plants": [], "tasks_to_create": [], "overwatering_risk": [], "heatwave_timing": []}

        weather = self.context.get("weather")
        today = datetime.now(timezone.utc).date()
        upcoming_watering = []
        tasks_to_create = []
        overwatering_risk = []
        heatwave_timing = []

        for plant in plants:
            last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
            base_date = last_watered + timedelta(days=plant["frequency_days"])

            if weather:
                next_water, reason = adjust_watering_date(
                    base_date, plant["frequency_days"], plant, weather)
            else:
                next_water = base_date
                reason = ""

            days_until = (next_water - today).days

            effective_interval = (next_water - last_watered).days
            location = plant.get("location", "indoor")
            if location == "indoor" and weather:
                sensitivity = plant.get("water_sensitivity", "medium")
                threshold = SENSITIVITY_THRESHOLDS.get(sensitivity, 0.6)
                if effective_interval < plant["frequency_days"] * threshold:
                    overwatering_risk.append({
                        "name": plant["name"],
                        "effective_interval": effective_interval,
                        "normal_frequency": plant["frequency_days"],
                    })

            if location == "outdoor" and weather and is_heatwave_incoming(weather) and days_until <= 1:
                heatwave_timing.append(plant["name"])

            if days_until <= 7:
                entry = {
                    "name": plant["name"],
                    "next_water_date": next_water.isoformat(),
                    "days_until": days_until,
                }
                if reason:
                    entry["adjustment"] = reason
                upcoming_watering.append(entry)

            if days_until <= 0:
                tasks_to_create.append({
                    "name": plant["name"],
                    "task_content": f"Water {plant['name']}",
                    "due_date": next_water.isoformat(),
                })

        return {
            "plants": upcoming_watering,
            "tasks_to_create": tasks_to_create,
            "overwatering_risk": overwatering_risk,
            "heatwave_timing": heatwave_timing,
        }

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

        # Save HTML output (model emits the same HTML it sent via gmail_send)
        output_path = REPO_ROOT / "output" / f"daily-briefing-{today}.html"
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
        overwatering_risk = plant_data.get("overwatering_risk", [])
        heatwave_timing = plant_data.get("heatwave_timing", [])

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

        advisory_section = ""
        if overwatering_risk:
            risk_lines = ["⚠ Watering Advisory — weather is driving unusually frequent watering for:"]
            for r in overwatering_risk:
                risk_lines.append(
                    f"- {r['name']}: next watering in {r['effective_interval']}d "
                    f"(normal cycle: {r['normal_frequency']}d)"
                )
            advisory_section = "\n".join(risk_lines)

        heatwave_section = ""
        if heatwave_timing:
            lines = ["⚠ Heatwave look-ahead — water these outdoor plants this evening or tomorrow morning before temperatures peak:"]
            for name in heatwave_timing:
                lines.append(f"- {name}")
            heatwave_section = "\n".join(lines)

        return f"""{base_prompt}

## Pre-computed Plant Data
{plant_section}

## Watering Advisory
{advisory_section if advisory_section else "No overwatering concerns."}

## Heatwave Timing
{heatwave_section if heatwave_section else "No heatwave look-ahead."}

## Plant Todoist Tasks
{task_instructions if task_instructions else "No plant tasks to create."}

## Today's Date
{today}
"""
