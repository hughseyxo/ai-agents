"""Tests for agents.plant_model — Pydantic models, PlantStore, PlantIntelligenceResult."""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from agents.plant_model import (
    AssessmentRecord,
    FrequencyChange,
    Plant,
    PlantIntelligenceResult,
    PlantStore,
)


# --- Plant model ---

class TestPlantModel:
    def test_minimal_valid(self):
        p = Plant(
            name="Monstera",
            frequency_days=7,
            baseline_frequency_days=7,
            last_watered=date(2026, 6, 1),
        )
        assert p.water_sensitivity == "medium"
        assert p.needs_photo is False
        assert p.last_assessment is None

    def test_frequency_clamped_high(self):
        p = Plant(name="X", frequency_days=99, baseline_frequency_days=99, last_watered=date.today())
        assert p.frequency_days == 30
        assert p.baseline_frequency_days == 30

    def test_frequency_clamped_low(self):
        p = Plant(name="X", frequency_days=0, baseline_frequency_days=0, last_watered=date.today())
        assert p.frequency_days == 1

    def test_invalid_location(self):
        with pytest.raises(ValidationError):
            Plant(name="X", frequency_days=7, baseline_frequency_days=7,
                  last_watered=date.today(), location="balcony")

    def test_invalid_sensitivity(self):
        with pytest.raises(ValidationError):
            Plant(name="X", frequency_days=7, baseline_frequency_days=7,
                  last_watered=date.today(), water_sensitivity="extreme")

    def test_roundtrip_serialization(self):
        p = Plant(
            name="Lavender",
            frequency_days=12,
            baseline_frequency_days=14,
            last_watered=date(2026, 6, 1),
            location="outdoor",
            sunlight="full sun",
            water_sensitivity="low",
            needs_photo=True,
        )
        dumped = p.model_dump(mode="json")
        assert dumped["last_watered"] == "2026-06-01"
        restored = Plant.model_validate(dumped)
        assert restored == p


# --- PlantStore.migrate ---

class TestPlantStoreMigrate:
    def test_legacy_dict_missing_baseline(self):
        raw = {"name": "Fern", "frequency_days": 5, "last_watered": "2026-06-01",
               "location": "indoor", "sunlight": "", "water_sensitivity": "medium"}
        p = PlantStore._migrate(raw)
        assert p.baseline_frequency_days == 5

    def test_legacy_dict_bad_location_coerced(self):
        raw = {"name": "X", "frequency_days": 7, "last_watered": "2026-06-01",
               "location": "roof", "sunlight": ""}
        p = PlantStore._migrate(raw)
        assert p.location == "indoor"

    def test_legacy_dict_bad_sensitivity_coerced(self):
        raw = {"name": "X", "frequency_days": 7, "last_watered": "2026-06-01",
               "location": "indoor", "water_sensitivity": "extreme"}
        p = PlantStore._migrate(raw)
        assert p.water_sensitivity == "medium"

    def test_assessment_migrated(self):
        raw = {
            "name": "X", "frequency_days": 7, "last_watered": "2026-06-01",
            "location": "indoor",
            "last_assessment": {"date": "2026-05-20", "summary": "looks healthy", "status": "Healthy"},
        }
        p = PlantStore._migrate(raw)
        assert p.last_assessment is not None
        assert p.last_assessment.summary == "looks healthy"

    def test_broken_assessment_skipped(self):
        raw = {
            "name": "X", "frequency_days": 7, "last_watered": "2026-06-01",
            "location": "indoor",
            "last_assessment": {"date": "not-a-date"},
        }
        p = PlantStore._migrate(raw)
        assert p.last_assessment is None


# --- PlantStore read/write ---

@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    s = PlantStore(db_path)
    yield s
    s.close()


def _make_plant(name="Monstera", freq=7):
    return Plant(name=name, frequency_days=freq, baseline_frequency_days=freq,
                 last_watered=date(2026, 6, 1), location="indoor")


class TestPlantStore:
    def test_empty_db_returns_empty_list(self, store):
        assert store.get_plants() == []

    def test_save_and_get(self, store):
        plants = [_make_plant("Monstera"), _make_plant("Lavender", 14)]
        store.save_plants(plants)
        loaded = store.get_plants()
        assert len(loaded) == 2
        assert loaded[0].name == "Monstera"

    def test_get_plant_case_insensitive(self, store):
        store.save_plants([_make_plant("Monstera Deliciosa")])
        p = store.get_plant("monstera deliciosa")
        assert p is not None
        assert p.name == "Monstera Deliciosa"

    def test_get_plant_substring(self, store):
        store.save_plants([_make_plant("Monstera Deliciosa")])
        p = store.get_plant("monstera")
        assert p is not None

    def test_get_plant_not_found(self, store):
        store.save_plants([_make_plant("Monstera")])
        assert store.get_plant("Cactus") is None

    def test_update_plant(self, store):
        store.save_plants([_make_plant("Monstera", freq=7)])
        updated = _make_plant("Monstera", freq=10)
        result = store.update_plant(updated)
        assert result is True
        loaded = store.get_plant("Monstera")
        assert loaded.frequency_days == 10

    def test_update_plant_not_found(self, store):
        store.save_plants([_make_plant("Monstera")])
        result = store.update_plant(_make_plant("Cactus"))
        assert result is False

    def test_migration_on_read(self, store):
        store._db.set_state("daily-briefing", "plants", [
            {"name": "OldPlant", "frequency_days": 5, "last_watered": "2026-06-01",
             "location": "indoor"}
        ])
        plants = store.get_plants()
        assert plants[0].baseline_frequency_days == 5


# --- PlantIntelligenceResult ---

_VALID_JSON = json.dumps({
    "plants": [
        {"name": "Monstera", "status": "Healthy", "notes": ["looking good"],
         "needs_photo": False, "frequency_change": None},
        {"name": "Lavender", "status": "Stressed", "notes": ["wilting"],
         "needs_photo": True,
         "frequency_change": {"days": 10, "reason": "consistent wilt"}},
    ],
    "pruning": [{"name": "Oleander", "action": "deadhead", "reason": "reblooming"}],
    "tasks_created": ["Prune Oleander"],
    "email_sent": True,
})


class TestPlantIntelligenceResult:
    def test_valid_parse(self):
        result = PlantIntelligenceResult.from_llm_output(_VALID_JSON)
        assert len(result.plants) == 2
        assert result.plants[0].status == "Healthy"
        assert result.plants[1].frequency_change.days == 10
        assert result.email_sent is True

    def test_code_fence_stripped(self):
        fenced = f"```json\n{_VALID_JSON}\n```"
        result = PlantIntelligenceResult.from_llm_output(fenced)
        assert len(result.plants) == 2

    def test_minimal_valid(self):
        minimal = json.dumps({"plants": [], "pruning": [], "tasks_created": [], "email_sent": False})
        result = PlantIntelligenceResult.from_llm_output(minimal)
        assert result.plants == []

    def test_optional_fields_default(self):
        partial = json.dumps({
            "plants": [{"name": "X", "status": "Healthy", "needs_photo": False}],
            "email_sent": False,
        })
        result = PlantIntelligenceResult.from_llm_output(partial)
        assert result.plants[0].notes == []
        assert result.pruning == []

    def test_invalid_json_raises(self):
        with pytest.raises(Exception):
            PlantIntelligenceResult.from_llm_output("not json at all")

    def test_invalid_status_raises(self):
        bad = json.dumps({
            "plants": [{"name": "X", "status": "UnknownStatus", "needs_photo": False}],
            "email_sent": False,
        })
        with pytest.raises(ValidationError):
            PlantIntelligenceResult.from_llm_output(bad)
