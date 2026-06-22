"""Pydantic models for plant data and intelligence output.

PlantStore wraps AgentDB with typed read/write and legacy-dict migration.
Intelligence models provide structured parsing of the LLM intelligence run output.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from .db import AgentDB, DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

_STATUS_VALUES = ("Healthy", "Stressed", "Overwatered", "Underwatered", "Concerning")
_SENSITIVITY_VALUES = ("high", "medium", "low")
_LOCATION_VALUES = ("indoor", "outdoor")


class AssessmentRecord(BaseModel):
    date: date
    summary: str
    status: str = ""


class Plant(BaseModel):
    name: str
    frequency_days: int
    baseline_frequency_days: int
    last_watered: date
    location: Literal["indoor", "outdoor"] = "indoor"
    sunlight: str = ""
    water_sensitivity: Literal["high", "medium", "low"] = "medium"
    last_assessment: Optional[AssessmentRecord] = None
    needs_photo: bool = False

    @field_validator("frequency_days", "baseline_frequency_days")
    @classmethod
    def clamp_frequency(cls, v: int) -> int:
        return max(1, min(30, v))


# --- Intelligence output models ---

class FrequencyChange(BaseModel):
    days: int
    reason: str


class PlantIntelligenceEntry(BaseModel):
    name: str
    status: Literal["Healthy", "Stressed", "Overwatered", "Underwatered", "Concerning"]
    notes: list[str] = []
    needs_photo: bool = False
    frequency_change: Optional[FrequencyChange] = None


class PruningEntry(BaseModel):
    name: str
    action: str
    reason: str = ""


class PlantIntelligenceResult(BaseModel):
    plants: list[PlantIntelligenceEntry]
    pruning: list[PruningEntry] = []
    email_sent: bool = False

    @classmethod
    def from_llm_output(cls, output: str) -> "PlantIntelligenceResult":
        """Parse LLM output, tolerating ```json code fences."""
        text = output.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[^\n]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
        return cls.model_validate_json(text)


# --- PlantStore ---

class PlantStore:
    """Typed read/write access to the plant list in AgentDB."""

    DB_AGENT = "daily-briefing"
    DB_KEY = "plants"

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self._db = AgentDB(db_path)

    def close(self) -> None:
        self._db.close()

    def get_plants(self) -> list[Plant]:
        raw = self._db.get_state(self.DB_AGENT, self.DB_KEY) or []
        return [self._migrate(r) for r in raw]

    def save_plants(self, plants: list[Plant]) -> None:
        self._db.set_state(self.DB_AGENT, self.DB_KEY, [p.model_dump(mode="json") for p in plants])

    def get_plant(self, name: str) -> Optional[Plant]:
        name_lower = name.lower().strip()
        plants = self.get_plants()
        return (
            next((p for p in plants if p.name.lower() == name_lower), None)
            or next((p for p in plants if name_lower in p.name.lower()), None)
        )

    def update_plant(self, updated: Plant) -> bool:
        """Replace an existing plant by name. Returns False if not found."""
        plants = self.get_plants()
        for i, p in enumerate(plants):
            if p.name.lower() == updated.name.lower():
                plants[i] = updated
                self.save_plants(plants)
                return True
        return False

    @staticmethod
    def _migrate(raw: dict) -> Plant:
        """Coerce a legacy dict into a Plant, filling missing fields with safe defaults."""
        freq = int(raw.get("frequency_days") or 7)
        baseline = int(raw.get("baseline_frequency_days") or freq)
        last_w = raw.get("last_watered") or date.today().isoformat()

        location = raw.get("location", "indoor")
        if location not in _LOCATION_VALUES:
            location = "indoor"

        sensitivity = raw.get("water_sensitivity", "medium")
        if sensitivity not in _SENSITIVITY_VALUES:
            sensitivity = "medium"

        assessment = None
        if raw.get("last_assessment"):
            a = raw["last_assessment"]
            try:
                assessment = AssessmentRecord(
                    date=a.get("date", date.today().isoformat()),
                    summary=a.get("summary", ""),
                    status=a.get("status", ""),
                )
            except Exception as e:
                logger.warning(
                    "bad last_assessment for plant %r, skipping: %s",
                    raw.get("name"), e,
                )

        return Plant(
            name=raw.get("name", "Unknown"),
            frequency_days=freq,
            baseline_frequency_days=baseline,
            last_watered=last_w,
            location=location,
            sunlight=raw.get("sunlight", ""),
            water_sensitivity=sensitivity,
            last_assessment=assessment,
            needs_photo=bool(raw.get("needs_photo", False)),
        )
