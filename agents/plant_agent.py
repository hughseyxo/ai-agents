"""Master Plant Agent — frequency-gated steps for weather and intelligence."""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import BaseAgent, REPO_ROOT
from .plant_model import PlantIntelligenceResult
from .plant_weather import weather_adjusted_frequency, apply_frequency_step
from .plant_profiles import (
    append_frequency_history, append_intelligence_note,
    write_health_assessment, write_profile_atomic,
    upsert_frontmatter, rewrite_section,
)
from .weather import fetch_weather


def _safe_date(value):
    """Parse a YYYY-MM-DD date; return None on a missing/malformed value so one
    bad plant record doesn't abort the whole step."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _build_status_table(plants: list, weather_cache: dict, today) -> str:
    """Build the markdown plant-status table, ordered chronologically by next water date."""
    entries = []
    for plant in plants:
        cache = weather_cache.get(plant["name"], {})
        adjusted_str = cache.get("adjusted_date", "")
        reason = cache.get("adjustment_reason", "") or "—"

        if adjusted_str:
            next_water = _safe_date(adjusted_str)
        else:
            last_watered = _safe_date(plant.get("last_watered"))
            next_water = (last_watered + timedelta(days=plant["frequency_days"])
                          if last_watered else None)
        if next_water is None:
            continue  # unparseable date — skip this plant rather than crash the table

        days_until = (next_water - today).days
        if days_until < 0:
            status = f"⚠ Overdue {abs(days_until)}d"
        elif days_until == 0:
            status = "💧 Due today"
        else:
            status = f"In {days_until}d"

        entries.append((next_water, plant["name"],
                        f"| {plant['name']} | {next_water} | {status} | {reason} |"))

    if not entries:
        return "No plants tracked."

    entries.sort(key=lambda e: (e[0], e[1]))
    header = "| Plant | Next Water | Status | Adjustment |\n|---|---|---|---|"
    return header + "\n" + "\n".join(e[2] for e in entries)


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
    write_profile_atomic(path, content)


def _extract_json_array(raw: str) -> list:
    """Parse a JSON array from LLM output, tolerating code fences and surrounding prose.

    Raises ValueError if no JSON array can be recovered.
    """
    if not raw:
        raise ValueError("empty output")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    # Fallback: grab the first [...] block out of surrounding prose
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    raise ValueError("no JSON array found in output")


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
            {"name": "send_status_email", "fn": self._send_status_email, "side_effects": True},
            {"name": "intelligence_run", "fn": self._intelligence_run, "side_effects": True},
        ]

    def plan(self):
        plants = self.db.get_state("daily-briefing", "plants") or []
        weather_cache = {r["plant_name"]: r for r in self.db.get_plant_weather_cache()}
        return {"plants": plants, "weather_cache": weather_cache}

    def _weather_update(self):
        weather = fetch_weather()
        self.context["weather"] = weather  # reused by _intelligence_run
        plants = self.context["plan"]["plants"]
        changed = False
        # Baseline migration is weather-independent — always run
        for plant in plants:
            if "baseline_frequency_days" not in plant:
                plant["baseline_frequency_days"] = plant["frequency_days"]
                changed = True
        if weather is None:
            print(f"[{self.name}] Weather fetch failed — keeping existing frequency adjustments", file=sys.stderr)
            if changed:
                self.db.set_state("daily-briefing", "plants", plants)
            return {"updated": 0, "skipped": "weather_unavailable"}
        for plant in plants:
            new_freq, reason = weather_adjusted_frequency(plant, weather)
            if new_freq != plant.get("frequency_days"):
                plant["frequency_days"] = new_freq
                changed = True
            last_watered = _safe_date(plant.get("last_watered"))
            if last_watered is None:
                continue  # bad date on this plant; skip rather than abort the run
            next_date = last_watered + timedelta(days=plant["frequency_days"])
            self.db.upsert_plant_weather_cache(plant["name"], next_date.isoformat(), reason)
        if changed:
            self.db.set_state("daily-briefing", "plants", plants)
        return {"updated": len(plants)}

    def _send_status_email(self):
        if not self._gate("send_status_email", 24):
            return {"skipped": True}

        plants = self.context["plan"]["plants"]
        weather_cache = self.context["plan"]["weather_cache"]
        today = datetime.now(timezone.utc).date()

        plant_table = _build_status_table(plants, weather_cache, today)

        prompt_path = REPO_ROOT / "agents" / "prompts" / "plant_status_email.md"
        prompt = prompt_path.read_text()
        prompt = prompt.replace("{{plant_table}}", plant_table).replace("{{today}}", today.isoformat())
        # Let synthesize() failures propagate: BaseAgent._execute_step records the
        # step error and marks the run partial_failure. Gate stays unmarked on
        # failure (we never reach _mark_ran), so it retries next run.
        self.synthesize(prompt)
        self._mark_ran("send_status_email")
        return {"sent": True, "plants": len(plants)}

    def _intelligence_run(self):
        if not self._gate("intelligence_run", 24):
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

        # Let synthesize() failures propagate so the run is marked partial_failure.
        # Gate stays unmarked on failure (we never reach _mark_ran) → retries next run.
        output = self.synthesize(prompt)
        self._apply_intelligence_output(output, plants)
        self._mark_ran("intelligence_run")
        return {"ran": True}

    def _apply_intelligence_output(self, output: str, plants: list):
        PLANTS_DIR = REPO_ROOT / "docs" / "plants"
        today = datetime.now(timezone.utc).date().isoformat()
        weather = self.context.get("weather")

        try:
            result = PlantIntelligenceResult.from_llm_output(output)
        except Exception as e:
            print(f"[{self.name}] Intelligence output parse failed: {e}", file=sys.stderr)
            print(f"[{self.name}] Raw output (first 500 chars): {output[:500]}", file=sys.stderr)
            return

        freq_changed = False
        needs_photo_changed = False

        for entry in result.plants:
            plant_name = entry.name
            plant = next((p for p in plants if p["name"].lower() == plant_name.lower()), None)

            # Append intelligence notes to profile
            if entry.notes:
                if plant:
                    slug = plant_name.lower().replace(" ", "-").replace("/", "-")
                    profile_path = PLANTS_DIR / f"{slug}.md"
                    if not profile_path.exists():
                        _create_profile_doc(profile_path, plant)
                notes_text = "\n".join(f"- {n}" for n in entry.notes)
                append_intelligence_note(plant_name, f"### {today}\n{notes_text}")

            # Apply frequency change
            if entry.frequency_change and plant:
                target = entry.frequency_change.days
                reason = entry.frequency_change.reason
                old = plant.get("baseline_frequency_days", plant["frequency_days"])
                new = apply_frequency_step(old, target)
                if new != old:
                    plant["baseline_frequency_days"] = new
                    plant["frequency_days"], _ = weather_adjusted_frequency(plant, weather)
                    ok = append_frequency_history(
                        plant["name"], old, new, f"intelligence: {reason}".rstrip(": ").strip()
                    )
                    if not ok:
                        print(f"[{self.name}] append_frequency_history skipped — no profile for {plant['name']}", file=sys.stderr)
                    freq_changed = True

            # Update needs_photo flag
            if plant and entry.needs_photo != plant.get("needs_photo", False):
                plant["needs_photo"] = entry.needs_photo
                needs_photo_changed = True

            # Regenerate frontmatter projection + curate Current Observations
            if plant:
                fm_fields = {
                    "type": "plant",
                    "location": plant.get("location", "indoor"),
                    "sunlight": plant.get("sunlight", "unknown"),
                    "water_sensitivity": plant.get("water_sensitivity", "medium"),
                    "baseline_frequency_days": plant.get("baseline_frequency_days", plant["frequency_days"]),
                    "effective_frequency_days": plant["frequency_days"],
                    "last_watered": plant.get("last_watered"),
                    "needs_photo": plant.get("needs_photo", False),
                    "latest_health": plant.get("last_assessment"),
                    "tags": [
                        "plant",
                        plant.get("location", "indoor"),
                        f"sensitivity/{plant.get('water_sensitivity', 'medium')}",
                    ],
                }
                upsert_frontmatter(plant_name, fm_fields)
                if entry.notes:
                    obs_body = "".join(f"- {n}\n" for n in entry.notes)
                    rewrite_section(plant_name, "Current Observations", obs_body)

        if freq_changed or needs_photo_changed:
            self.db.set_state("daily-briefing", "plants", plants)

        # Append pruning notes and persist as pending actions for daily briefing
        if result.pruning:
            self.set_state("pending_plant_actions", [
                {"plant": p.name, "action": p.action, "reason": p.reason, "date": today}
                for p in result.pruning
            ])
        for pruning in result.pruning:
            action_text = pruning.action
            if pruning.reason:
                action_text += f" ({pruning.reason})"
            append_intelligence_note(pruning.name, f"### {today} (pruning)\n- {action_text}")

    def report(self) -> str:
        weather = self.context.get("weather_update", {})
        intel = self.context.get("intelligence_run", {})

        parts = [f"plant-agent: {weather.get('updated', 0)} plant(s) weather-updated"]
        if intel.get("ran"):
            parts.append("intelligence run complete")
        return ", ".join(parts)
