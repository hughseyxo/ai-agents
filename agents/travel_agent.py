"""Travel research + planning agent.

Two modes:
  search (default) -- Claude searches for flights, hotels, activities, emails report
  plan             -- User provides existing bookings; Claude builds itinerary, emails report
"""

import json
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from .base import BaseAgent, REPO_ROOT
from .weather import fetch_weather

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


class TravelAgent(BaseAgent):
    name = "travel-agent"
    schedule = ""  # on-demand only
    model = "claude-haiku-4-5"

    def configure(self, args):
        """Accept CLI args before run()."""
        self.mode = getattr(args, "mode", "search")
        self.destination = args.destination
        self.origin = getattr(args, "origin", None)
        self.checkin = args.checkin
        self.checkout = args.checkout
        self.existing_flights = getattr(args, "flights", None)
        self.existing_hotel = getattr(args, "hotel", None)

    def plan(self) -> dict:
        return {
            "mode": self.mode,
            "destination": self.destination,
            "origin": self.origin,
            "checkin": self.checkin,
            "checkout": self.checkout,
        }

    def steps(self) -> list[dict]:
        steps = [{"name": "fetch_weather", "fn": self._fetch_destination_weather}]
        if self.mode == "plan":
            steps.append({"name": "plan_trip", "fn": self._plan_trip, "side_effects": True})
        else:
            steps.append({"name": "research_travel", "fn": self._research_travel, "side_effects": True})
        steps.append({"name": "save_report", "fn": self._save_report})
        return steps

    def _geocode(self, destination: str) -> tuple[float, float] | None:
        """Resolve destination name to lat/lon via Open-Meteo geocoding (no API key)."""
        try:
            url = f"{GEOCODE_URL}?name={quote(destination)}&count=1&language=en&format=json"
            with urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            r = data["results"][0]
            return r["latitude"], r["longitude"]
        except Exception as e:
            print(f"[travel-agent] Geocode failed for '{destination}': {e}", file=sys.stderr)
            return None

    def _fetch_destination_weather(self):
        coords = self._geocode(self.destination)
        if coords:
            lat, lon = coords
            return fetch_weather(lat=lat, lon=lon)
        return None

    def _research_travel(self):
        dedup_key = f"{self.destination}:{self.checkin}:{self.checkout}:search"
        if self.is_duplicate("email_sent", dedup_key):
            print(f"[travel-agent] Email already sent for {dedup_key}, skipping", file=sys.stderr)
            return {"skipped": True}
        result = self.synthesize(self._build_search_prompt())
        self.mark_seen("email_sent", dedup_key)
        return result

    def _plan_trip(self):
        dedup_key = f"{self.destination}:{self.checkin}:{self.checkout}:plan"
        if self.is_duplicate("email_sent", dedup_key):
            print(f"[travel-agent] Email already sent for {dedup_key}, skipping", file=sys.stderr)
            return {"skipped": True}
        result = self.synthesize(self._build_plan_prompt())
        self.mark_seen("email_sent", dedup_key)
        return result

    def _save_report(self):
        step_key = "research_travel" if self.mode == "search" else "plan_trip"
        content = self.context.get(step_key)
        if not content or isinstance(content, dict):
            return "skipped (no content or deduped)"
        dest_slug = self.destination.lower().replace(" ", "-")
        filename = f"travel-{dest_slug}-{self.checkin}.html"
        output_path = REPO_ROOT / "output" / filename
        output_path.write_text(content, encoding="utf-8")
        return str(output_path)

    def report(self) -> str:
        path = self.context.get("save_report", "unknown")
        return f"Travel report saved and emailed: {path}"

    def _build_search_prompt(self) -> str:
        template = (REPO_ROOT / "agents" / "prompts" / "travel_agent_search.md").read_text()
        weather_json = json.dumps(self.context.get("fetch_weather"), indent=2) if self.context.get("fetch_weather") else "unavailable"
        return template.format(
            destination=self.destination,
            origin=self.origin or "unspecified",
            checkin=self.checkin,
            checkout=self.checkout,
            weather=weather_json,
        )

    def _build_plan_prompt(self) -> str:
        template = (REPO_ROOT / "agents" / "prompts" / "travel_agent_plan.md").read_text()
        weather_json = json.dumps(self.context.get("fetch_weather"), indent=2) if self.context.get("fetch_weather") else "unavailable"
        return template.format(
            destination=self.destination,
            checkin=self.checkin,
            checkout=self.checkout,
            weather=weather_json,
            flights=self.existing_flights or "not provided",
            hotel=self.existing_hotel or "not provided",
        )
