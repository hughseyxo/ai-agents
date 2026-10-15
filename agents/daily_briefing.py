"""Daily Briefing Agent.

Python handles: lifecycle, SQLite state, dedup.
LLM CLI (with MCP access) handles: calendar/todoist fetching, formatting, email sending.
"""

import sys
from datetime import date, datetime, timedelta, timezone

from . import calendar_client
from .base import BaseAgent, REPO_ROOT
from .plant_model import PlantStore

# Personal-calendar entries shorter than this are noise (reminders, declined
# invites). On-call shifts are exempt — see _format_calendar_event.
MIN_EVENT_MINUTES = 15


class DailyBriefingAgent(BaseAgent):
    name = "daily-briefing"
    schedule = "5 4 * * *"
    model = "claude-haiku-4-5"
    # Reads todoist and sends the briefing itself (see _run_briefing). Calendar
    # data is fetched in Python (_build_calendar_block) and injected, so the
    # calendar MCP server is deliberately NOT reachable from the prompt.
    # Gmail is scoped to send only — this agent never needs to read mail.
    mcp_config = ".mcp.json"
    # ToolSearch is required: MCP tools are deferred, so without it gmail_send
    # is never reachable and the briefing silently never sends.
    allowed_tools = ["ToolSearch", "mcp__todoist", "mcp__gmail__gmail_send"]

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

        # LLM CLI has MCP access — it fetches todoist, formats, and sends
        output = self.synthesize(prompt)

        # Save HTML output (model emits the same HTML it sent via gmail_send)
        output_path = REPO_ROOT / "output" / f"daily-briefing-{today}.html"
        output_path.write_text(self._extract_html(output))

        self.mark_seen("email_sent", today)
        return {"sent": True, "output_path": str(output_path)}

    @staticmethod
    def _extract_html(output: str) -> str:
        """Trim model preamble/epilogue around the saved HTML.

        The prompt asks for bare HTML, but both CLIs still sometimes wrap it in
        commentary or a ``` fence, which leaves output/daily-briefing-*.html
        unopenable in a browser. The email itself is unaffected — it goes out
        via gmail_send. Returns the input unchanged if no document is found, so
        a malformed response is still saved for debugging rather than lost.
        """
        start = output.find("<!DOCTYPE html")
        if start == -1:
            start = output.find("<html")
        if start == -1:
            return output
        end = output.rfind("</html>")
        return output[start:end + len("</html>")] if end != -1 else output[start:]

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

    def _build_calendar_block(self, today: str) -> str:
        """Build a pre-computed calendar section for injection into the prompt.

        Fetched in Python rather than by the LLM over MCP: the calendar ids and
        windows used to be prose instructions, and when the on-call id went
        stale the resulting 404 was absorbed by the model instead of surfacing.
        Here a dead calendar renders an explicit warning line.

        All-day events are included — the old prompt filtered them out, which is
        why 'Today' showed nothing but plant tasks (most personal-calendar
        entries are birthdays, anniversaries and trips).
        """
        today_date = date.fromisoformat(today)
        horizon = today_date + timedelta(days=30)

        windows = {
            "today": (today, today),
            "upcoming": ((today_date + timedelta(days=1)).isoformat(), horizon.isoformat()),
        }
        calendars = (
            (calendar_client.PRIMARY_CALENDAR_ID, "Personal calendar", False),
            (calendar_client.ONCALL_CALENDAR_ID, "On-call calendar", True),
        )

        sections: dict[str, list[str]] = {"today": [], "upcoming": []}
        warnings: list[str] = []

        for cal_id, label, is_oncall in calendars:
            for window, (start_day, end_day) in windows.items():
                try:
                    time_min, time_max = calendar_client.day_bounds(start_day, end_day)
                    items, error = calendar_client.fetch_events(cal_id, time_min, time_max)
                except Exception as e:  # fetch_events shouldn't raise, but a
                    # calendar outage must never take the whole briefing down.
                    items, error = [], f"{type(e).__name__}: {e}"
                if error:
                    warning = f"- ⚠️ {label} unavailable: {error}"
                    if warning not in warnings:
                        warnings.append(warning)
                    continue
                for item in items:
                    line = self._format_calendar_event(item, is_oncall, window)
                    if line and line not in sections[window]:
                        sections[window].append(line)

        if not sections["today"] and not sections["upcoming"] and not warnings:
            return ""

        lines = [
            "## Calendar",
            "Already fetched — do NOT call any calendar tool.",
            "Render `Today` items in the 📅 Today section and `Coming up` items in 📆 Coming Up.",
        ]
        if sections["today"]:
            lines.append("\nToday:")
            lines.extend(sorted(sections["today"]))
        if sections["upcoming"]:
            lines.append("\nComing up (next 30 days):")
            lines.extend(sorted(sections["upcoming"]))
        if warnings:
            lines.append("\nFetch problems (render verbatim in the Today section):")
            lines.extend(warnings)
        return "\n".join(lines)

    @staticmethod
    def _format_calendar_event(item: dict, is_oncall: bool, window: str) -> str | None:
        """One pre-formatted line, or None if the event should be dropped."""
        event = calendar_client.parse_event(item)
        if event is None:
            return None
        # Sub-15-minute personal entries are calendar noise (declined invites,
        # reminders). On-call shifts are always kept regardless of length.
        if (not is_oncall and not event["all_day"]
                and (event["duration_minutes"] or 0) < MIN_EVENT_MINUTES):
            return None

        prefix = "[ON CALL] " if is_oncall else ""
        if event["all_day"]:
            span = event["date"]
            if event["end_date"] != event["date"]:
                span += f"–{event['end_date']}"
            when = "(all day)" if window == "today" else f"{span} (all day)"
        else:
            clock = f"{event['start_time']}–{event['end_time']}"
            when = clock if window == "today" else f"{event['date']} {clock}"
        return f"- {when} — {prefix}{event['summary']}"

    def _build_prompt(self, today: str) -> str:
        """Build the Claude CLI prompt."""
        prompt_path = REPO_ROOT / "agents" / "prompts" / "daily_briefing.md"
        base_prompt = prompt_path.read_text()
        calendar_block = self._build_calendar_block(today)
        plant_block = self._build_plant_care_block(today)

        return f"""{base_prompt}

## Today's Date
{today}

{calendar_block}

{plant_block}
"""
