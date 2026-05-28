"""Master Plant Agent — frequency-gated steps for weather, watering sync, tasks, photos, and intelligence."""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .base import BaseAgent, REPO_ROOT
from .plant_weather import adjust_watering_date, is_heatwave_incoming
from .weather import fetch_weather

PERSONAL_PROJECT_ID = "6Crf3cH2RF5v86wc"

_SYNC_PROMPT = f"""
You are a data-fetch step. Your only job is to find recently completed plant watering tasks in Todoist and return JSON.

Use mcp__todoist__find-completed-tasks to find completed tasks in project {PERSONAL_PROJECT_ID} completed in the last 30 days.

Filter to tasks whose content starts with "Water " (case-insensitive).

Return ONLY valid JSON — no markdown, no explanation, no other text:
[{{"name": "Plant Name", "completed_date": "YYYY-MM-DD"}}]

Extract "Plant Name" by stripping the "Water " prefix from the task content.
Use the task's completion date as "completed_date" in YYYY-MM-DD format.
If no matching completed tasks found, return: []
"""

_CREATE_TASK_PROMPT = """\
Create a Todoist task in project {project_id} if it does not already exist:
  Content: "{content}", due: {due}, priority: p4

Use mcp__todoist__find-tasks to search for an existing task with content "{content}" \
due on {due}. Only call mcp__todoist__add-tasks if no such task exists.
Return only the word "created" or "exists".
"""


def _create_profile_doc(path: Path, plant: dict):
    """Create a minimal profile doc for a plant that doesn't have one yet."""
    name = plant["name"]
    content = f"""# {name}

## Plant Info
- Location: {plant.get('location', 'unknown').capitalize()} | Sunlight: {plant.get('sunlight', 'unknown')}
- Water sensitivity: {plant.get('water_sensitivity', 'medium')} | Base frequency: {plant['frequency_days']} days

## Observed Behaviour
<!-- Free text updated by intelligence run — notable patterns, visual cues -->

## Health Assessments
<!-- Photo assessments appended here -->

## Frequency History
| Date | Change | Reason |
|---|---|---|

## Intelligence Notes
<!-- Appended by each intelligence run -->
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class PlantAgent(BaseAgent):
    name = "plant-agent"
    schedule = "0 * * * *"
    model = "claude-haiku-4-5"

    def _gate(self, key: str, hours: int) -> bool:
        """Return True if step should run (>hours since last run, or never run)."""
        last = self.get_state(f"last_{key}")
        if not last:
            return True
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return elapsed > hours * 3600

    def _mark_ran(self, key: str):
        self.set_state(f"last_{key}", datetime.now(timezone.utc).isoformat())

    def steps(self):
        return [
            {"name": "weather_update", "fn": self._weather_update},
            {"name": "sync_watering", "fn": self._sync_watering},
            {"name": "create_tasks", "fn": self._create_tasks, "side_effects": True},
            {"name": "photo_requests", "fn": self._photo_requests, "side_effects": True},
            {"name": "send_status_email", "fn": self._send_status_email, "side_effects": True},
            {"name": "intelligence_run", "fn": self._intelligence_run, "side_effects": True},
        ]

    def plan(self):
        plants = self.db.get_state("daily-briefing", "plants") or []
        weather_cache = {r["plant_name"]: r for r in self.db.get_plant_weather_cache()}
        return {"plants": plants, "weather_cache": weather_cache}

    def _weather_update(self):
        weather = fetch_weather()
        if not weather:
            return {"skipped": True}

        plants = self.context["plan"]["plants"]
        updated = 0
        for plant in plants:
            last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
            base_date = last_watered + timedelta(days=plant["frequency_days"])
            adjusted, reason = adjust_watering_date(base_date, plant["frequency_days"], plant, weather)
            self.db.upsert_plant_weather_cache(plant["name"], adjusted.isoformat(), reason)
            updated += 1

        return {"updated": updated}

    def _sync_watering(self):
        if not self._gate("sync_watering", 24):
            return {"skipped": True}

        try:
            output = self.synthesize(_SYNC_PROMPT)
        except Exception as e:
            print(f"[{self.name}] sync_watering LLM call failed: {e}", file=sys.stderr)
            return {"synced": 0, "error": str(e)}

        raw = output.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[^\n]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        try:
            completions = json.loads(raw)
        except json.JSONDecodeError:
            return {"synced": 0, "error": "bad_json"}

        plants = self.db.get_state("daily-briefing", "plants") or []
        synced = 0
        updated = False

        for completion in completions:
            name = completion.get("name", "")
            completed_date = completion.get("completed_date", "")
            if not name or not completed_date:
                continue
            for plant in plants:
                if plant["name"].lower() == name.lower():
                    if completed_date > plant.get("last_watered", ""):
                        plant["last_watered"] = completed_date
                        synced += 1
                        updated = True
                    break

        if updated:
            self.db.set_state("daily-briefing", "plants", plants)

        self._mark_ran("sync_watering")
        return {"synced": synced}

    def _create_tasks(self):
        if not self._gate("create_tasks", 12):
            return {"skipped": True}

        plants = self.context["plan"]["plants"]
        weather_cache = self.context["plan"]["weather_cache"]
        today = datetime.now(timezone.utc).date()
        weather = fetch_weather()

        tasks_to_create = []
        for plant in plants:
            last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
            base_date = last_watered + timedelta(days=plant["frequency_days"])

            if weather:
                adjusted, reason = adjust_watering_date(base_date, plant["frequency_days"], plant, weather)
            else:
                adjusted = base_date

            if adjusted <= today:
                tasks_to_create.append({"name": plant["name"], "due": adjusted.isoformat()})
            elif (plant.get("location") == "outdoor"
                  and weather and is_heatwave_incoming(weather)
                  and adjusted <= today + timedelta(days=1)):
                tasks_to_create.append({"name": plant["name"], "due": adjusted.isoformat(), "heatwave": True})

        if not tasks_to_create:
            self._mark_ran("create_tasks")
            return {"skipped": True, "reason": "no_due_plants"}

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

        self._mark_ran("create_tasks")
        return {"created": created, "skipped": skipped}

    def _photo_requests(self):
        if not self._gate("photo_requests", 24):
            return {"skipped": True}

        token = os.environ.get("CONCIERGE_BOT_TOKEN", "")
        user_id = os.environ.get("TELEGRAM_USER_ID", "")
        if not token or not user_id:
            return {"skipped": True, "reason": "no_telegram_config"}

        photo_rates = self.get_state("photo_request_rates") or {}
        plants = self.context["plan"]["plants"]
        sent = 0

        for plant in plants:
            name = plant["name"]

            last_req = photo_rates.get(name)
            if last_req:
                days_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_req)).days
                if days_since < 3:
                    continue

            should_request = False
            reason = ""

            if plant.get("needs_photo"):
                should_request = True
                reason = "flagged by intelligence analysis"

            last_assessment = plant.get("last_assessment")
            if not last_assessment:
                should_request = True
                reason = reason or "no health assessment on record"
            elif last_assessment.get("date"):
                try:
                    last_date = datetime.fromisoformat(last_assessment["date"]).replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - last_date).days > 14:
                        should_request = True
                        reason = reason or f"last assessment was {(datetime.now(timezone.utc) - last_date).days} days ago"
                except ValueError:
                    pass

            if should_request:
                msg = f"📸 Can you send a photo of your {name}? ({reason})"
                try:
                    resp = requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": user_id, "text": msg},
                        timeout=10,
                    )
                    if resp.ok:
                        photo_rates[name] = datetime.now(timezone.utc).isoformat()
                        sent += 1
                except Exception as e:
                    print(f"[{self.name}] Photo request failed for {name}: {e}", file=sys.stderr)

        self.set_state("photo_request_rates", photo_rates)
        self._mark_ran("photo_requests")
        return {"sent": sent}

    def _send_status_email(self):
        if not self._gate("send_status_email", 24):
            return {"skipped": True}

        plants = self.context["plan"]["plants"]
        weather_cache = self.context["plan"]["weather_cache"]
        today = datetime.now(timezone.utc).date()

        rows = []
        header = "| Plant | Next Water | Status | Adjustment |\n|---|---|---|---|"
        for plant in sorted(plants, key=lambda p: p["name"]):
            cache = weather_cache.get(plant["name"], {})
            adjusted_str = cache.get("adjusted_date", "")
            reason = cache.get("adjustment_reason", "") or "—"

            if adjusted_str:
                next_water = datetime.strptime(adjusted_str, "%Y-%m-%d").date()
            else:
                last_watered = datetime.strptime(plant["last_watered"], "%Y-%m-%d").date()
                next_water = last_watered + timedelta(days=plant["frequency_days"])

            days_until = (next_water - today).days
            if days_until < 0:
                status = f"⚠ Overdue {abs(days_until)}d"
            elif days_until == 0:
                status = "💧 Due today"
            else:
                status = f"In {days_until}d"

            rows.append(f"| {plant['name']} | {next_water} | {status} | {reason} |")

        plant_table = header + "\n" + "\n".join(rows) if rows else "No plants tracked."

        prompt_path = REPO_ROOT / "agents" / "prompts" / "plant_status_email.md"
        prompt = prompt_path.read_text()
        prompt = prompt.replace("{{plant_table}}", plant_table).replace("{{today}}", today.isoformat())
        try:
            self.synthesize(prompt)
        except Exception as e:
            print(f"[{self.name}] Failed to send status email: {e}", file=sys.stderr)
            return {"skipped": True, "error": str(e)}
        self._mark_ran("send_status_email")
        return {"sent": True, "plants": len(plants)}

    def _intelligence_run(self):
        if not self._gate("intelligence_run", 72):
            return {"skipped": True}

        plants = self.context["plan"]["plants"]
        weather_cache_list = self.db.get_plant_weather_cache()
        today = datetime.now(timezone.utc).date()

        PLANTS_DIR = REPO_ROOT / "docs" / "plants"
        profiles = []
        for plant in plants:
            slug = plant["name"].lower().replace(" ", "-").replace("/", "-")
            profile_path = PLANTS_DIR / f"{slug}.md"
            if profile_path.exists():
                profiles.append(f"### {plant['name']}\n{profile_path.read_text()}")
            else:
                profiles.append(
                    f"### {plant['name']}\n"
                    f"No profile yet. Location: {plant.get('location', 'unknown')}, "
                    f"frequency: {plant['frequency_days']} days, "
                    f"sensitivity: {plant.get('water_sensitivity', 'medium')}."
                )

        notes_path = REPO_ROOT / "docs" / "agent-notes.md"
        agent_notes = ""
        if notes_path.exists():
            raw = notes_path.read_text()
            agent_notes = raw[-6000:] if len(raw) > 6000 else raw

        prompt_path = REPO_ROOT / "agents" / "prompts" / "plant_intelligence.md"
        prompt = (prompt_path.read_text()
            .replace("{{plant_state_json}}", json.dumps(plants, indent=2))
            .replace("{{weather_cache_json}}", json.dumps(weather_cache_list, indent=2))
            .replace("{{plant_profiles}}", "\n\n".join(profiles))
            .replace("{{agent_notes}}", agent_notes)
            .replace("{{today}}", today.isoformat()))

        try:
            output = self.synthesize(prompt)
        except Exception as e:
            print(f"[{self.name}] Intelligence run LLM failed: {e}", file=sys.stderr)
            self._mark_ran("intelligence_run")
            return {"skipped": True, "error": str(e)}
        self._apply_intelligence_output(output, plants)
        self._mark_ran("intelligence_run")
        return {"ran": True}

    def _apply_intelligence_output(self, output: str, plants: list):
        PLANTS_DIR = REPO_ROOT / "docs" / "plants"
        today = datetime.now(timezone.utc).date().isoformat()

        for m in re.finditer(r'\[PROFILE:([^\]]+)\](.*?)\[/PROFILE\]', output, re.DOTALL):
            plant_name = m.group(1).strip()
            notes = m.group(2).strip()
            if not notes:
                continue
            slug = plant_name.lower().replace(" ", "-").replace("/", "-")
            profile_path = PLANTS_DIR / f"{slug}.md"
            if not profile_path.exists():
                plant = next((p for p in plants if p["name"].lower() == plant_name.lower()), None)
                if plant:
                    _create_profile_doc(profile_path, plant)
            if profile_path.exists():
                content = profile_path.read_text()
                entry = f"\n### {today}\n{notes}\n"
                if "## Intelligence Notes" in content:
                    content = content.replace(
                        "<!-- Appended by each intelligence run -->",
                        f"<!-- Appended by each intelligence run -->{entry}"
                    )
                else:
                    content += f"\n## Intelligence Notes\n<!-- Appended by each intelligence run -->{entry}"
                profile_path.write_text(content)

        pruning_m = re.search(r'\[PRUNING\](.*?)\[/PRUNING\]', output, re.DOTALL)
        if pruning_m:
            for line in pruning_m.group(1).strip().splitlines():
                line = line.strip()
                if not line or " — " not in line:
                    continue
                plant_name, action = line.split(" — ", 1)
                plant_name = plant_name.strip()
                slug = plant_name.lower().replace(" ", "-").replace("/", "-")
                profile_path = PLANTS_DIR / f"{slug}.md"
                if profile_path.exists():
                    content = profile_path.read_text()
                    entry = f"\n### {today} (pruning)\n- {action.strip()}\n"
                    if "## Intelligence Notes" in content:
                        content = content.replace(
                            "<!-- Appended by each intelligence run -->",
                            f"<!-- Appended by each intelligence run -->{entry}"
                        )
                    else:
                        content += f"\n## Intelligence Notes\n<!-- Appended by each intelligence run -->{entry}"
                    profile_path.write_text(content)

        needs_photo_m = re.search(r'\[NEEDS_PHOTO\](.*?)\[/NEEDS_PHOTO\]', output, re.DOTALL)
        if needs_photo_m:
            flagged_raw = needs_photo_m.group(1).strip()
            flagged = {n.strip().lower() for n in flagged_raw.split(",") if n.strip()}
            updated = []
            changed = False
            for plant in plants:
                new_flag = plant["name"].lower() in flagged
                if new_flag != plant.get("needs_photo", False):
                    changed = True
                updated.append({**plant, "needs_photo": new_flag})
            if changed:
                self.db.set_state("daily-briefing", "plants", updated)

    def report(self) -> str:
        weather = self.context.get("weather_update", {})
        sync = self.context.get("sync_watering", {})
        tasks = self.context.get("create_tasks", {})
        intel = self.context.get("intelligence_run", {})

        parts = [f"plant-agent: {weather.get('updated', 0)} plant(s) weather-updated"]
        if not sync.get("skipped"):
            parts.append(f"{sync.get('synced', 0)} watering(s) synced")
        if not tasks.get("skipped"):
            parts.append(f"{tasks.get('created', 0)} task(s) created")
        if intel.get("ran"):
            parts.append("intelligence run complete")
        return ", ".join(parts)
