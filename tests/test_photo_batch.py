import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "telegram-bot"))

from agents.db import AgentDB
from agents.plant_model import PlantStore, Plant
from agents import plant_profiles as pp
import agents.photo_batch as pb


@pytest.fixture
def db(tmp_path):
    d = AgentDB(db_path=tmp_path / "agents.db")
    yield d
    d.close()


@pytest.fixture
def store(tmp_path):
    s = PlantStore(db_path=tmp_path / "agents.db")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def isolate_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "PHOTO_STORE_DIR", tmp_path / "plant-photos")
    monkeypatch.setattr(pb, "BATCH_TEMP_DIR", tmp_path / "plant-photo-batches")
    monkeypatch.setattr(pp, "PLANTS_DIR", tmp_path / "plants")


@pytest.fixture
def seeded_plant(store):
    plant = Plant(
        name="Monstera Deliciosa", frequency_days=7, baseline_frequency_days=7,
        last_watered=date(2026, 7, 1), location="indoor",
    )
    store.add(plant)
    return plant


# --- save_plant_photo ---


def test_save_plant_photo_writes_file_and_db_row(db):
    path = pb.save_plant_photo(db, "Monstera Deliciosa", b"fakejpegbytes",
                                assessment_summary="Healthy", assessment_status="Healthy")

    assert Path(path).exists()
    assert Path(path).read_bytes() == b"fakejpegbytes"
    rows = db.get_recent_plant_photos("Monstera Deliciosa", limit=10)
    assert len(rows) == 1
    assert rows[0]["assessment_status"] == "Healthy"


def test_save_plant_photo_prunes_beyond_keep_and_deletes_files(db):
    paths = [pb.save_plant_photo(db, "Monstera Deliciosa", f"photo-{i}".encode(), keep=3)
             for i in range(5)]

    remaining = db.get_recent_plant_photos("Monstera Deliciosa", limit=10)
    assert len(remaining) == 3
    # The two oldest files should have been deleted from disk.
    assert not Path(paths[0]).exists()
    assert not Path(paths[1]).exists()
    assert Path(paths[-1]).exists()


def test_get_trend_photo_paths_oldest_first(db):
    p1 = pb.save_plant_photo(db, "Monstera Deliciosa", b"1")
    p2 = pb.save_plant_photo(db, "Monstera Deliciosa", b"2")
    p3 = pb.save_plant_photo(db, "Monstera Deliciosa", b"3")

    trend_paths = pb.get_trend_photo_paths(db, "Monstera Deliciosa", limit=3)

    assert trend_paths == [p1, p2, p3]


# --- create_batch_job ---


def test_create_batch_job_persists_files_and_job_row(db):
    job_id = pb.create_batch_job(db, [b"photo-a", b"photo-b"])

    job = db.get_batch_job(job_id)
    assert job["status"] == "running"
    assert len(job["items"]) == 2
    for item in job["items"]:
        assert Path(item["temp_path"]).exists()
        assert item["status"] == "pending"


# --- _process_item ---


def test_process_item_pauses_on_usage_limit(db, store, seeded_plant, tmp_path):
    temp_path = tmp_path / "photo.jpg"
    temp_path.write_bytes(b"x")
    item = {"temp_path": str(temp_path), "status": "pending"}

    with patch.object(pb, "identify_and_assess", return_value=(None, True)):
        updated_item, usage_limit_hit = pb._process_item(item, [seeded_plant.model_dump(mode="json")], store, db)

    assert usage_limit_hit is True
    assert updated_item["status"] == "pending"  # unchanged — will retry this same item


def test_process_item_marks_unmatched_when_no_confident_match(db, store, seeded_plant, tmp_path):
    temp_path = tmp_path / "photo.jpg"
    temp_path.write_bytes(b"x")
    item = {"temp_path": str(temp_path), "status": "pending"}

    with patch.object(pb, "identify_and_assess", return_value=({"matched_plant": None}, False)):
        updated_item, usage_limit_hit = pb._process_item(item, [seeded_plant.model_dump(mode="json")], store, db)

    assert usage_limit_hit is False
    assert updated_item["status"] == "unmatched"


def test_process_item_persists_assessment_on_match(db, store, seeded_plant, tmp_path):
    temp_path = tmp_path / "photo.jpg"
    temp_path.write_bytes(b"x")
    item = {"temp_path": str(temp_path), "status": "pending"}

    call1_result = {"matched_plant": "Monstera Deliciosa", "confidence": "high"}
    trend_response = (
        '{"status": "Healthy", "summary": "Looking great", "observations": [], '
        '"watering_recommendation": "on_schedule", "care_actions": [], '
        '"noteworthy": false, "profile_notes": "### note"}'
    )

    with patch.object(pb, "identify_and_assess", return_value=(call1_result, False)), \
         patch.object(pb, "assess_image", return_value=trend_response):
        updated_item, usage_limit_hit = pb._process_item(item, [seeded_plant.model_dump(mode="json")], store, db)

    assert usage_limit_hit is False
    assert updated_item["status"] == "done"
    assert updated_item["matched_plant"] == "Monstera Deliciosa"

    refreshed = store.get_plant("Monstera Deliciosa")
    assert refreshed.last_assessment.status == "Healthy"
    assert refreshed.needs_photo is False
    photos = db.get_recent_plant_photos("Monstera Deliciosa", limit=10)
    assert len(photos) == 1


def test_process_item_unmatched_when_matched_name_not_a_real_plant(db, store, seeded_plant, tmp_path):
    temp_path = tmp_path / "photo.jpg"
    temp_path.write_bytes(b"x")
    item = {"temp_path": str(temp_path), "status": "pending"}

    with patch.object(pb, "identify_and_assess", return_value=({"matched_plant": "Some Unknown Plant"}, False)):
        updated_item, usage_limit_hit = pb._process_item(item, [seeded_plant.model_dump(mode="json")], store, db)

    assert usage_limit_hit is False
    assert updated_item["status"] == "unmatched"


# --- run_batch_job (async state machine) ---


@pytest.mark.asyncio
async def test_run_batch_job_completes_when_all_items_match(db, store, seeded_plant, tmp_path, monkeypatch):
    job_id = pb.create_batch_job(db, [b"photo-a"])
    trend_response = (
        '{"status": "Healthy", "summary": "Looking great", "observations": [], '
        '"watering_recommendation": "on_schedule", "care_actions": [], "noteworthy": false}'
    )

    with patch.object(pb, "identify_and_assess",
                       return_value=({"matched_plant": "Monstera Deliciosa", "confidence": "high"}, False)), \
         patch.object(pb, "assess_image", return_value=trend_response), \
         patch.object(pb, "PlantStore", return_value=store):
        await pb.run_batch_job(job_id, db=db)

    job = db.get_batch_job(job_id)
    assert job["status"] == "done"
    assert job["current_index"] == 1


@pytest.mark.asyncio
async def test_run_batch_job_pauses_then_resumes_after_ping(db, store, seeded_plant, tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "PING_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(pb, "MAX_PAUSE_SECONDS", 10)
    job_id = pb.create_batch_job(db, [b"photo-a"])
    trend_response = (
        '{"status": "Healthy", "summary": "ok", "observations": [], '
        '"watering_recommendation": "on_schedule", "care_actions": [], "noteworthy": false}'
    )

    call_count = {"n": 0}

    def fake_identify(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None, True  # usage limit hit on first attempt
        return {"matched_plant": "Monstera Deliciosa", "confidence": "high"}, False

    with patch.object(pb, "identify_and_assess", side_effect=fake_identify), \
         patch.object(pb, "assess_image", return_value=trend_response), \
         patch.object(pb, "PlantStore", return_value=store):
        await pb.run_batch_job(job_id, db=db)

    assert call_count["n"] == 2
    job = db.get_batch_job(job_id)
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_run_batch_job_fails_after_max_pause_window(db, store, tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "PING_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(pb, "MAX_PAUSE_SECONDS", 0.02)
    job_id = pb.create_batch_job(db, [b"photo-a"])

    with patch.object(pb, "identify_and_assess", return_value=(None, True)), \
         patch.object(pb, "PlantStore", return_value=store):
        await pb.run_batch_job(job_id, db=db)

    job = db.get_batch_job(job_id)
    assert job["status"] == "failed"


@pytest.mark.asyncio
async def test_run_batch_job_marks_failed_on_unhandled_exception(db, store, seeded_plant, tmp_path):
    job_id = pb.create_batch_job(db, [b"photo-a"])

    with patch.object(pb, "identify_and_assess", side_effect=RuntimeError("boom")), \
         patch.object(pb, "PlantStore", return_value=store):
        await pb.run_batch_job(job_id, db=db)  # must not raise

    job = db.get_batch_job(job_id)
    assert job["status"] == "failed"
    assert "boom" in job["pause_reason"]


@pytest.mark.asyncio
async def test_run_batch_job_persists_pause_seconds_across_a_fresh_call(db, store, seeded_plant, tmp_path, monkeypatch):
    # Simulates a service restart mid-pause: cumulative pause time already
    # spent must be read back from the DB, not reset to 0, so the 2h cap
    # can't be bypassed by repeated restarts.
    monkeypatch.setattr(pb, "PING_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(pb, "MAX_PAUSE_SECONDS", 0.015)
    job_id = pb.create_batch_job(db, [b"photo-a"])
    db.update_batch_job(job_id, paused_seconds=0.01)  # already spent most of the budget

    with patch.object(pb, "identify_and_assess", return_value=(None, True)), \
         patch.object(pb, "PlantStore", return_value=store):
        await pb.run_batch_job(job_id, db=db)

    job = db.get_batch_job(job_id)
    # Should fail on the very first pause attempt (0.01 already spent + 0.01
    # ping > 0.015 cap), not after another full budget's worth of pinging.
    assert job["status"] == "failed"


# --- assign_item ---


@pytest.mark.asyncio
async def test_assign_item_persists_assessment_for_unmatched_item(db, store, seeded_plant, tmp_path, monkeypatch):
    job_id = pb.create_batch_job(db, [b"photo-a"])
    job = db.get_batch_job(job_id)
    items = job["items"]
    items[0]["status"] = "unmatched"
    db.update_batch_job(job_id, items=items)

    trend_response = (
        '{"status": "Healthy", "summary": "assigned ok", "observations": [], '
        '"watering_recommendation": "on_schedule", "care_actions": [], "noteworthy": false}'
    )

    with patch.object(pb, "assess_image", return_value=trend_response), \
         patch.object(pb, "PlantStore", return_value=store):
        result = await pb.assign_item(job_id, 0, "Monstera Deliciosa", db=db)

    assert result["status"] == "done"
    assert result["matched_plant"] == "Monstera Deliciosa"
    job = db.get_batch_job(job_id)
    assert job["items"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_assign_item_rejects_item_not_yet_reached_by_running_job(db, store, seeded_plant, tmp_path):
    job_id = pb.create_batch_job(db, [b"photo-a", b"photo-b"])
    # status defaults to "running", current_index defaults to 0 — item 1
    # hasn't been reached yet, so assigning it now would race the loop.

    with pytest.raises(ValueError, match="still being processed"):
        await pb.assign_item(job_id, 1, "Monstera Deliciosa", db=db)


@pytest.mark.asyncio
async def test_assign_item_allows_item_already_passed_by_running_job(db, store, seeded_plant, tmp_path):
    job_id = pb.create_batch_job(db, [b"photo-a", b"photo-b"])
    job = db.get_batch_job(job_id)
    items = job["items"]
    items[0]["status"] = "unmatched"  # loop already passed over item 0
    db.update_batch_job(job_id, items=items, current_index=1)

    trend_response = (
        '{"status": "Healthy", "summary": "ok", "observations": [], '
        '"watering_recommendation": "on_schedule", "care_actions": [], "noteworthy": false}'
    )
    with patch.object(pb, "assess_image", return_value=trend_response), \
         patch.object(pb, "PlantStore", return_value=store):
        result = await pb.assign_item(job_id, 0, "Monstera Deliciosa", db=db)

    assert result["status"] == "done"


# --- resume_active_jobs ---


def test_resume_active_jobs_schedules_task_per_active_job(db):
    running_id = pb.create_batch_job(db, [b"a"])
    paused_id = pb.create_batch_job(db, [b"b"])
    db.update_batch_job(paused_id, status="paused")
    done_id = pb.create_batch_job(db, [b"c"])
    db.update_batch_job(done_id, status="done")

    with patch.object(pb.asyncio, "create_task", side_effect=lambda coro: coro.close()) as mock_create_task:
        job_ids = pb.resume_active_jobs(db=db)

    assert set(job_ids) == {running_id, paused_id}
    assert mock_create_task.call_count == 2
