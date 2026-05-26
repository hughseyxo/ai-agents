"""Plant Weather Agent — hourly weather-aware watering adjustment cache."""

import sys
from datetime import datetime, timedelta, timezone

from .base import BaseAgent
from .plant_weather import adjust_watering_date, is_heatwave_incoming
from .weather import fetch_weather

PERSONAL_PROJECT_ID = "6Crf3cH2RF5v86wc"

_CREATE_TASK_PROMPT = """\
Create a Todoist task in project {project_id} if it does not already exist:
  Content: "{content}", due: {due}, priority: p4

Use mcp__todoist__find-tasks to search for an existing task with content "{content}" \
due on {due}. Only call mcp__todoist__add-tasks if no such task exists.
Return only the word "created" or "exists".
"""


class PlantWeatherAgent(BaseAgent):
    name = "plant-weather"
    schedule = "0 * * * *"
    model = "claude-haiku-4-5"

    def plan(self):
        return {}

    def steps(self):
        return [
            {"name": "weather", "fn": self._fetch_weather},
            {"name": "update_adjustments", "fn": self._update_adjustments},
            {"name": "create_tasks", "fn": self._create_tasks, "side_effects": True},
        ]

    def report(self) -> str:
        adj = self.context.get("update_adjustments", {})
        tasks = self.context.get("create_tasks", {})
        if adj.get("skipped"):
            return "plant-weather: skipped — no weather data"
        created = tasks.get("created", 0)
        suffix = f", {created} task(s) created" if created else ""
        return f"plant-weather: updated {adj.get('updated', 0)} plant(s){suffix}"

    def _fetch_weather(self):
        weather = fetch_weather()
        self.context["weather"] = weather
        return weather

    def _update_adjustments(self):
        weather = self.context.get("weather")
        if not weather:
            return {"skipped": True, "updated": 0, "tasks_to_create": []}

        plants = self.db.get_state("daily-briefing", "plants") or []
        if not plants:
            return {"updated": 0, "tasks_to_create": []}

        today = datetime.now(timezone.utc).date()
        updated = 0
        tasks_to_create = []

        for plant in plants:
            last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
            base_date = last_watered + timedelta(days=plant["frequency_days"])
            adjusted, reason = adjust_watering_date(
                base_date, plant["frequency_days"], plant, weather
            )
            self.db.upsert_plant_weather_cache(
                plant["name"], adjusted.isoformat(), reason
            )
            updated += 1
            if adjusted <= today:
                tasks_to_create.append({"name": plant["name"], "due": adjusted.isoformat()})
            elif (plant.get("location") == "outdoor"
                  and is_heatwave_incoming(weather)
                  and adjusted <= today + timedelta(days=1)):
                tasks_to_create.append({
                    "name": plant["name"],
                    "due": adjusted.isoformat(),
                    "heatwave": True,
                })

        return {"updated": updated, "tasks_to_create": tasks_to_create}

    def _create_tasks(self):
        result = self.context.get("update_adjustments") or {}
        tasks_to_create = result.get("tasks_to_create", [])
        today = datetime.now(timezone.utc).date()
        created = 0
        skipped = 0

        for task in tasks_to_create:
            name = task["name"]
            due = task["due"]
            is_heatwave_task = task.get("heatwave", False)
            content = f"Water {name} before heatwave" if is_heatwave_task else f"Water {name}"
            dedup_key = f"{name}:{today}"

            if self.is_duplicate("task_created", dedup_key):
                skipped += 1
                continue

            prompt = _CREATE_TASK_PROMPT.format(
                project_id=PERSONAL_PROJECT_ID,
                content=content,
                due=due,
            )
            try:
                self.synthesize(prompt)
                self.mark_seen("task_created", dedup_key)
                created += 1
            except Exception as e:
                print(f"[{self.name}] Failed to create task for {name}: {e}", file=sys.stderr)

        return {"created": created, "skipped": skipped}
