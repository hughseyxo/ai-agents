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

    def test_broken_assessment_is_logged(self, caplog):
        """C1: a corrupt assessment must be logged (log-and-skip), not silently swallowed."""
        raw = {
            "name": "Fern", "frequency_days": 7, "last_watered": "2026-06-01",
            "location": "indoor",
            "last_assessment": {"date": "not-a-date"},
        }
        with caplog.at_level("WARNING", logger="agents.plant_model"):
            p = PlantStore._migrate(raw)
        assert p.last_assessment is None
        assert any("Fern" in r.getMessage() for r in caplog.records)


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


# --- Row-per-plant storage (Task 8) ---

def test_store_migrates_legacy_blob_to_rows(tmp_path):
    from agents.db import AgentDB
    from agents.plant_model import PlantStore
    db = AgentDB(tmp_path / "t.db")
    db.set_state("daily-briefing", "plants", [{"name": "Aloe", "frequency_days": 10}])
    db.close()
    store = PlantStore(tmp_path / "t.db")
    assert [p.name for p in store.get_plants()] == ["Aloe"]
    assert store._db.get_plant_rows()  # rows populated, legacy blob no longer authoritative
    store.close()


def test_update_fields_touches_only_one_row(tmp_path):
    from agents.plant_model import PlantStore, Plant
    from datetime import date
    store = PlantStore(tmp_path / "t.db")
    store.add(Plant(name="Aloe", frequency_days=10, baseline_frequency_days=10, last_watered=date.today()))
    store.add(Plant(name="Yucca", frequency_days=14, baseline_frequency_days=14, last_watered=date.today()))
    out = store.update_fields("aloe", frequency_days=5)
    assert out.frequency_days == 5
    assert store.get_plant("Yucca").frequency_days == 14
    store.close()


class TestAgentDBPlantRows:
    """AgentDB row helpers backing PlantStore (upsert overwrite, delete, replace)."""

    def test_get_plant_rows_empty(self, tmp_path):
        from agents.db import AgentDB
        db = AgentDB(tmp_path / "t.db")
        assert db.get_plant_rows() == []
        db.close()

    def test_upsert_plant_row_overwrites(self, tmp_path):
        from agents.db import AgentDB
        db = AgentDB(tmp_path / "t.db")
        db.upsert_plant_row("Aloe", {"name": "Aloe", "frequency_days": 10})
        db.upsert_plant_row("Aloe", {"name": "Aloe", "frequency_days": 20})
        rows = db.get_plant_rows()
        assert len(rows) == 1
        assert rows[0]["frequency_days"] == 20
        db.close()

    def test_upsert_plant_row_keyed_lowercase(self, tmp_path):
        from agents.db import AgentDB
        db = AgentDB(tmp_path / "t.db")
        db.upsert_plant_row("Aloe Vera", {"name": "Aloe Vera", "frequency_days": 10})
        db.upsert_plant_row("ALOE VERA", {"name": "Aloe Vera", "frequency_days": 30})
        assert len(db.get_plant_rows()) == 1
        db.close()

    def test_delete_plant_row(self, tmp_path):
        from agents.db import AgentDB
        db = AgentDB(tmp_path / "t.db")
        db.upsert_plant_row("Aloe", {"name": "Aloe", "frequency_days": 10})
        db.upsert_plant_row("Yucca", {"name": "Yucca", "frequency_days": 14})
        db.delete_plant_row("aloe")
        rows = db.get_plant_rows()
        assert [r["name"] for r in rows] == ["Yucca"]
        db.close()

    def test_delete_plant_row_missing_is_noop(self, tmp_path):
        from agents.db import AgentDB
        db = AgentDB(tmp_path / "t.db")
        db.delete_plant_row("nonexistent")  # should not raise
        assert db.get_plant_rows() == []
        db.close()

    def test_replace_plant_rows_clears_previous(self, tmp_path):
        from agents.db import AgentDB
        db = AgentDB(tmp_path / "t.db")
        db.upsert_plant_row("Aloe", {"name": "Aloe", "frequency_days": 10})
        db.replace_plant_rows([{"name": "Yucca", "frequency_days": 14}])
        rows = db.get_plant_rows()
        assert [r["name"] for r in rows] == ["Yucca"]
        db.close()


class TestPlantStoreRowOps:
    """PlantStore.add / remove / update_fields / get_plants_raw."""

    def test_add_persists_plant(self, store):
        store.add(_make_plant("Aloe"))
        assert [p.name for p in store.get_plants()] == ["Aloe"]

    def test_add_second_plant_does_not_drop_first(self, store):
        store.add(_make_plant("Aloe"))
        store.add(_make_plant("Yucca"))
        assert sorted(p.name for p in store.get_plants()) == ["Aloe", "Yucca"]

    def test_remove_existing_returns_true(self, store):
        store.add(_make_plant("Aloe"))
        assert store.remove("aloe") is True
        assert store.get_plants() == []

    def test_remove_missing_returns_false(self, store):
        store.add(_make_plant("Aloe"))
        assert store.remove("Cactus") is False
        assert len(store.get_plants()) == 1

    def test_update_fields_not_found_returns_none(self, store):
        assert store.update_fields("Nonexistent", frequency_days=5) is None

    def test_update_fields_rename_moves_row(self, store):
        store.add(_make_plant("Aloe"))
        updated = store.update_fields("aloe", name="Aloe Vera")
        assert updated.name == "Aloe Vera"
        assert store.get_plant("Aloe Vera") is not None
        # old row key gone, no duplicate under the old name
        assert len(store.get_plants()) == 1

    def test_get_plants_raw_returns_dicts(self, store):
        store.add(_make_plant("Aloe"))
        raw = store.get_plants_raw()
        assert isinstance(raw, list)
        assert raw[0]["name"] == "Aloe"
